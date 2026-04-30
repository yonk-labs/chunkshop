//! Fastembed-backed embedder.
//!
//! Two paths:
//!
//! 1. **Stock-variant path** — for models where fastembed-rs's built-in
//!    registry already matches what we want (BGE non-quantized, MiniLM, etc.).
//!    Resolves through `resolve_model_name` and uses `TextEmbedding::try_new`.
//!
//! 2. **User-defined path (bit-exact)** — for `Xenova/bge-base-en-v1.5-int8`
//!    and `Xenova/bge-small-en-v1.5-int8`, where the goal is byte-identical
//!    output vs Python. Hand-rolls the ORT session because fastembed-rs's
//!    `try_new_from_user_defined` hardcodes `with_intra_threads(available_parallelism())`,
//!    which makes the reduction order CPU-count-dependent and breaks bit-
//!    exactness across machines. We pin `with_intra_threads(1)` for these two
//!    int8 models and replicate fastembed's tokenize → infer → CLS-pool →
//!    L2-normalize pipeline.

use std::collections::HashMap;

use anyhow::{anyhow, Context, Result};
use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};
use ndarray::{s, Array2};
use ort::session::{builder::GraphOptimizationLevel, Session};
use ort::value::Value;
use tokenizers::{PaddingParams, PaddingStrategy, Tokenizer, TruncationParams};
use tracing::info;

use crate::config::FastembedEmbedderConfig;
use crate::hf_cache::{fetch_user_defined_files, HfModelFiles};

pub struct FastembedEmbedder {
    cfg: FastembedEmbedderConfig,
    backend: Backend,
    /// Cumulative wall time spent inside `embed()`. Mirrors Python's
    /// `FastembedProvider.embed_seconds`. Used by the bakeoff to break out
    /// the embedder's portion of total cell wall time.
    embed_seconds: f64,
}

enum Backend {
    Stock(TextEmbedding),
    UserDefined(UserDefinedRunner),
}

/// Pooling strategy for the user-defined ONNX path. Stock fastembed-rs
/// variants pool internally; this enum only governs the hand-rolled forward.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pooling {
    /// Take the [CLS] token's hidden state. Used by BGE family + most BERT-derived retrieval models.
    Cls,
    /// Mean of token-level hidden states, masked to non-padding tokens.
    /// Used by sentence-transformers / e5 / etc.
    Mean,
}

fn parse_pooling(s: &str) -> Result<Pooling> {
    match s {
        "cls" => Ok(Pooling::Cls),
        "mean" => Ok(Pooling::Mean),
        other => Err(anyhow!(
            "embedder.pooling must be 'cls' or 'mean', got {other:?}"
        )),
    }
}

struct UserDefinedRunner {
    session: Session,
    tokenizer: Tokenizer,
    need_token_type_ids: bool,
    pooling: Pooling,
}

/// Returns `Some((repo, onnx_path))` when `model_name` is a Xenova int8 variant
/// we have a bit-exact path for. Otherwise `None`.
fn user_defined_source(model_name: &str) -> Option<(&'static str, &'static str)> {
    match model_name {
        "Xenova/bge-base-en-v1.5-int8" => {
            Some(("Xenova/bge-base-en-v1.5", "onnx/model_quantized.onnx"))
        }
        "Xenova/bge-small-en-v1.5-int8" => {
            Some(("Xenova/bge-small-en-v1.5", "onnx/model_quantized.onnx"))
        }
        _ => None,
    }
}

impl FastembedEmbedder {
    pub fn new(cfg: FastembedEmbedderConfig) -> Result<Self> {
        // Priority order for embedder dispatch:
        //   1. BYO mode (cfg.hf_repo set): user-defined ONNX path, runtime
        //      pooling per cfg.pooling. No registry lookups.
        //   2. Hardcoded user_defined_source (Xenova int8 BGE): bit-near-exact
        //      hand-rolled CLS-pooled path.
        //   3. fastembed-rs stock variants (resolve_model_name).
        if cfg.is_byo() {
            // Already validated by FastembedEmbedderConfig::validate() at config-load.
            let repo = cfg.hf_repo.as_deref().expect("BYO repo present");
            let onnx_path = cfg.onnx_path.as_deref().expect("BYO onnx_path present");
            let pooling = parse_pooling(&cfg.pooling)?;
            // Honor cfg.threads for BYO. Default 1 if unset — conservative
            // for shared boxes, but users can opt into multi-thread.
            let intra = cfg.threads.unwrap_or(1);
            let runner = build_user_defined_runner(repo, onnx_path, pooling, intra)?;
            info!(
                "embedder loaded (BYO, YAML-driven): {} (dim={}, repo={}, file={}, pooling={:?})",
                cfg.model_name, cfg.dim, repo, onnx_path, pooling
            );
            return Ok(Self {
                cfg,
                backend: Backend::UserDefined(runner),
                embed_seconds: 0.0,
            });
        }

        if let Some((repo, onnx_path)) = user_defined_source(&cfg.model_name) {
            // Hardcoded Xenova int8 path stays at intra_threads=1 by default
            // for bit-near-exact parity vs Python (parity tests depend on
            // this). YAML can override via `threads:` if user prioritizes
            // speed and accepts the parity drift.
            let intra = cfg.threads.unwrap_or(1);
            let runner = build_user_defined_runner(repo, onnx_path, Pooling::Cls, intra)?;
            info!(
                "embedder loaded (user-defined, bit-exact): {} (dim={}, repo={}, file={})",
                cfg.model_name, cfg.dim, repo, onnx_path
            );
            return Ok(Self {
                cfg,
                backend: Backend::UserDefined(runner),
                embed_seconds: 0.0,
            });
        }

        let variant = resolve_model_name(&cfg.model_name)?;
        let opts = InitOptions::new(variant).with_show_download_progress(true);
        let model = TextEmbedding::try_new(opts)
            .with_context(|| format!("initialising fastembed model {:?}", cfg.model_name))?;
        info!(
            "embedder loaded (stock variant): {} (dim={})",
            cfg.model_name, cfg.dim
        );
        Ok(Self {
            cfg,
            backend: Backend::Stock(model),
            embed_seconds: 0.0,
        })
    }

    /// Cumulative wall time spent in `embed()` calls so far.
    pub fn embed_seconds(&self) -> f64 {
        self.embed_seconds
    }

    pub fn dim(&self) -> usize {
        self.cfg.dim
    }

    /// Embed a batch of texts. Returns a flat `Vec<Vec<f32>>` ordered to match
    /// the input. Verifies the output dim matches the config `dim`. Tracks
    /// cumulative wall time in `self.embed_seconds`.
    pub fn embed(&mut self, texts: Vec<String>) -> Result<Vec<Vec<f32>>> {
        if texts.is_empty() {
            return Ok(Vec::new());
        }
        let t0 = std::time::Instant::now();
        let vecs = match &mut self.backend {
            Backend::Stock(model) => {
                let refs: Vec<&str> = texts.iter().map(String::as_str).collect();
                model
                    .embed(refs, Some(self.cfg.batch_size))
                    .context("fastembed embed call failed")?
            }
            Backend::UserDefined(runner) => {
                let mut out: Vec<Vec<f32>> = Vec::with_capacity(texts.len());
                for chunk in texts.chunks(self.cfg.batch_size.max(1)) {
                    let refs: Vec<&str> = chunk.iter().map(String::as_str).collect();
                    let batch = runner.embed_batch(&refs)?;
                    out.extend(batch);
                }
                out
            }
        };
        self.embed_seconds += t0.elapsed().as_secs_f64();
        if let Some(first) = vecs.first() {
            if first.len() != self.cfg.dim {
                return Err(anyhow!(
                    "model {} produced dim {}, config says dim={}",
                    self.cfg.model_name,
                    first.len(),
                    self.cfg.dim
                ));
            }
        }
        Ok(vecs)
    }
}

fn build_user_defined_runner(
    repo: &str,
    onnx_path: &str,
    pooling: Pooling,
    intra_threads: usize,
) -> Result<UserDefinedRunner> {
    let HfModelFiles {
        onnx,
        tokenizer,
        tokenizer_config,
        special_tokens_map: _,
        config,
    } = fetch_user_defined_files(repo, onnx_path)
        .with_context(|| format!("fetching user-defined files for {repo}"))?;

    // intra_threads = 1 is the bit-exactness setting. Caller passes 1 by
    // default for the Xenova int8 BGE bit-near-exact path (parity tests
    // depend on it). For BYO mode with `threads: 4` in YAML, the caller
    // passes 4 — bit-exactness isn't promised for arbitrary BYO models
    // anyway, and multi-threaded inference is 2-4× faster on big batches.
    // ORT optimization level Level3 stays the same regardless.
    let session = Session::builder()
        .map_err(|e| anyhow!("ort session builder: {e}"))?
        .with_optimization_level(GraphOptimizationLevel::Level3)
        .map_err(|e| anyhow!("ort with_optimization_level: {e}"))?
        .with_intra_threads(intra_threads)
        .map_err(|e| anyhow!("ort with_intra_threads({intra_threads}): {e}"))?
        .commit_from_memory(&onnx)
        .map_err(|e| anyhow!("commit ONNX from memory for {repo}: {e}"))?;

    let need_token_type_ids = session
        .inputs()
        .iter()
        .any(|i| i.name() == "token_type_ids");

    let mut tokenizer = Tokenizer::from_bytes(&tokenizer)
        .map_err(|e| anyhow!("tokenizer load failed: {e}"))?;

    // Mirror fastembed-py's tokenizer configuration: read pad token / id from
    // config.json + tokenizer_config.json, set BatchLongest padding + 512
    // truncation. Without this, our tokenizer pads per its bundled defaults
    // which can differ from Python's resulting attention_mask shape.
    let cfg_json: serde_json::Value = serde_json::from_slice(&config)
        .map_err(|e| anyhow!("parse config.json: {e}"))?;
    let tcfg_json: serde_json::Value = serde_json::from_slice(&tokenizer_config)
        .map_err(|e| anyhow!("parse tokenizer_config.json: {e}"))?;
    let pad_id = cfg_json
        .get("pad_token_id")
        .and_then(|v| v.as_u64())
        .unwrap_or(0) as u32;
    let pad_token = tcfg_json
        .get("pad_token")
        .and_then(|v| v.as_str())
        .unwrap_or("[PAD]")
        .to_string();
    let model_max_length = tcfg_json
        .get("model_max_length")
        .and_then(|v| v.as_f64())
        .unwrap_or(512.0)
        .min(512.0) as usize;

    tokenizer
        .with_padding(Some(PaddingParams {
            strategy: PaddingStrategy::BatchLongest,
            pad_token,
            pad_id,
            ..Default::default()
        }))
        .with_truncation(Some(TruncationParams {
            max_length: model_max_length,
            ..Default::default()
        }))
        .map_err(|e| anyhow!("configure tokenizer padding/truncation: {e}"))?;

    Ok(UserDefinedRunner {
        session,
        tokenizer,
        need_token_type_ids,
        pooling,
    })
}

impl UserDefinedRunner {
    fn embed_batch(&mut self, texts: &[&str]) -> Result<Vec<Vec<f32>>> {
        let encodings = self
            .tokenizer
            .encode_batch(texts.to_vec(), true)
            .map_err(|e| anyhow!("tokenize batch: {e}"))?;

        let batch_size = encodings.len();
        let seq_len = encodings
            .first()
            .ok_or_else(|| anyhow!("empty encodings"))?
            .len();

        let mut ids = Vec::with_capacity(batch_size * seq_len);
        let mut mask = Vec::with_capacity(batch_size * seq_len);
        let mut type_ids = Vec::with_capacity(batch_size * seq_len);
        for enc in &encodings {
            ids.extend(enc.get_ids().iter().map(|x| *x as i64));
            mask.extend(enc.get_attention_mask().iter().map(|x| *x as i64));
            type_ids.extend(enc.get_type_ids().iter().map(|x| *x as i64));
        }

        let ids_arr: Array2<i64> = Array2::from_shape_vec((batch_size, seq_len), ids)
            .context("ids array shape")?;
        let mask_arr: Array2<i64> = Array2::from_shape_vec((batch_size, seq_len), mask)
            .context("mask array shape")?;
        let type_ids_arr: Array2<i64> = Array2::from_shape_vec((batch_size, seq_len), type_ids)
            .context("type_ids array shape")?;

        // Clone mask for ORT input — we need to keep a copy for mean_pool below.
        // Keeping the clone close to the move site so the diff explains itself.
        let mask_for_ort = mask_arr.clone();
        let mut session_inputs = ort::inputs![
            "input_ids" => Value::from_array(ids_arr)?,
            "attention_mask" => Value::from_array(mask_for_ort)?,
        ];
        if self.need_token_type_ids {
            session_inputs.push((
                "token_type_ids".into(),
                Value::from_array(type_ids_arr)?.into(),
            ));
        }

        let outputs = self
            .session
            .run(session_inputs)
            .context("ort session.run")?;

        // Output is the model's last_hidden_state (BERT-style). Find the
        // first f32 tensor in the outputs map — for the Xenova int8 BGE
        // models there's one output ("last_hidden_state").
        let mut last_hidden: Option<ndarray::ArrayD<f32>> = None;
        for (_name, val) in outputs.iter() {
            if let Ok(arr) = val.try_extract_array::<f32>() {
                last_hidden = Some(arr.to_owned());
                break;
            }
        }
        let last_hidden = last_hidden
            .ok_or_else(|| anyhow!("no f32 output tensor found in session outputs"))?;

        // Expect shape (batch, seq, hidden). Pool per `self.pooling`.
        if last_hidden.ndim() != 3 {
            return Err(anyhow!(
                "expected 3D output (batch, seq, hidden), got ndim={}",
                last_hidden.ndim()
            ));
        }
        let pooled: ndarray::Array2<f32> = match self.pooling {
            Pooling::Cls => last_hidden.slice(s![.., 0, ..]).to_owned().into_dimensionality().unwrap(),
            Pooling::Mean => mean_pool(&last_hidden, &mask_arr)?,
        };

        let mut out = Vec::with_capacity(batch_size);
        for row in pooled.rows() {
            let v: Vec<f32> = row.to_vec();
            // Numpy's np.linalg.norm on f32 promotes to f64 internally for
            // the sum-of-squares accumulation; mirror that to maximize
            // cross-language parity. Final result is still f32.
            let norm_f64: f64 = v.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
            let denom = (norm_f64 as f32) + 1e-12_f32;
            let normalized: Vec<f32> = v.iter().map(|x| x / denom).collect();
            out.push(normalized);
        }
        Ok(out)
    }
}

/// Mean-pool the last_hidden output across non-padding tokens.
///
/// `last_hidden` shape: (batch, seq, hidden). `mask` shape: (batch, seq) with
/// 1 = real token, 0 = padding. Result shape: (batch, hidden). Mirrors what
/// sentence-transformers / fastembed do for mean-pooled models like e5 /
/// MiniLM. Without masking, padding tokens contribute zero-ish but real
/// values to the mean — for short inputs this materially distorts the vector.
fn mean_pool(
    last_hidden: &ndarray::ArrayD<f32>,
    mask: &ndarray::Array2<i64>,
) -> Result<ndarray::Array2<f32>> {
    let shape = last_hidden.shape();
    if shape.len() != 3 {
        return Err(anyhow!("mean_pool expects 3D last_hidden, got {:?}", shape));
    }
    let (batch, seq, hidden) = (shape[0], shape[1], shape[2]);
    if mask.shape() != [batch, seq] {
        return Err(anyhow!(
            "mean_pool: mask shape {:?} does not match last_hidden batch/seq ({}, {})",
            mask.shape(),
            batch,
            seq
        ));
    }
    let last3 = last_hidden
        .view()
        .into_dimensionality::<ndarray::Ix3>()
        .map_err(|e| anyhow!("mean_pool: cannot view as Ix3: {e}"))?;
    let mut out = ndarray::Array2::<f32>::zeros((batch, hidden));
    for b in 0..batch {
        let mut acc = vec![0.0_f32; hidden];
        let mut count: f32 = 0.0;
        for t in 0..seq {
            if mask[[b, t]] != 0 {
                count += 1.0;
                let row = last3.slice(s![b, t, ..]);
                for (i, v) in row.iter().enumerate() {
                    acc[i] += *v;
                }
            }
        }
        // If a row has zero unmasked tokens (shouldn't happen — tokenizers
        // emit at least the [CLS]/<s> token even for empty input), fall back
        // to the first token to avoid NaN. Otherwise divide.
        if count == 0.0 {
            let row = last3.slice(s![b, 0, ..]);
            for (i, v) in row.iter().enumerate() {
                out[[b, i]] = *v;
            }
        } else {
            for i in 0..hidden {
                out[[b, i]] = acc[i] / count;
            }
        }
    }
    Ok(out)
}

/// Map a Python-style `model_name` to a fastembed-rs `EmbeddingModel`. Only
/// reached for names that are NOT in `user_defined_source` — the int8 names
/// are handled by the user-defined path.
fn resolve_model_name(name: &str) -> Result<EmbeddingModel> {
    let mut table: HashMap<&str, EmbeddingModel> = HashMap::new();
    table.insert("BAAI/bge-base-en-v1.5", EmbeddingModel::BGEBaseENV15);
    table.insert("BAAI/bge-small-en-v1.5", EmbeddingModel::BGESmallENV15);
    table.insert("BAAI/bge-large-en-v1.5", EmbeddingModel::BGELargeENV15);
    table.insert(
        "sentence-transformers/all-MiniLM-L6-v2",
        EmbeddingModel::AllMiniLML6V2,
    );
    // The semantic chunker's default boundary model is the int8 MiniLM. We map
    // it to fastembed-rs's stock quantized AllMiniLML6V2Q (Qdrant fp32-optimized
    // ONNX, mean-pooled) — close enough for boundary detection. Full bit-near-
    // exact parity (Xenova int8 ONNX with mean pooling in our hand-rolled path)
    // would require extending the user-defined embedder code from MB-1 with a
    // mean-pooling branch — out of scope for the semantic-chunker brief because
    // semantic chunks are not promised byte-identical to Python anyway.
    table.insert(
        "sentence-transformers/all-MiniLM-L6-v2-int8",
        EmbeddingModel::AllMiniLML6V2Q,
    );
    // Nomic v1.5 long-context (8k tokens, 768 dim, mean-pooled internally by
    // fastembed-rs). The `-Q` suffix routes to the int8-quantized ONNX file
    // (`onnx/model_quantized.onnx` in the upstream HF repo) — same model_name
    // Python's fastembed accepts. Stock fastembed-rs handles pooling +
    // normalization, so no user-defined branch is needed.
    table.insert(
        "nomic-ai/nomic-embed-text-v1.5",
        EmbeddingModel::NomicEmbedTextV15,
    );
    table.insert(
        "nomic-ai/nomic-embed-text-v1.5-Q",
        EmbeddingModel::NomicEmbedTextV15Q,
    );

    table.get(name).cloned().ok_or_else(|| {
        anyhow!(
            "chunkshop-rs does not map model_name {name:?} to a fastembed-rs variant. \
             Supported (stock): BAAI/bge-base-en-v1.5, BAAI/bge-small-en-v1.5, \
             BAAI/bge-large-en-v1.5, sentence-transformers/all-MiniLM-L6-v2, \
             sentence-transformers/all-MiniLM-L6-v2-int8, \
             nomic-ai/nomic-embed-text-v1.5, nomic-ai/nomic-embed-text-v1.5-Q. \
             Bit-exact (user-defined): Xenova/bge-base-en-v1.5-int8, \
             Xenova/bge-small-en-v1.5-int8."
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `mean_pool` should average the unmasked tokens and ignore padding.
    /// Crafted so the right answer is hand-checkable.
    #[test]
    fn mean_pool_masks_padding() {
        // batch=1, seq=4, hidden=3. First 2 tokens are real, last 2 are padding.
        // Real values: [1,2,3], [4,5,6] → mean = [2.5, 3.5, 4.5].
        // Padding values would be [99,99,99] each — if mask is wrong we'd see
        // them dragging the mean toward 99.
        let last_hidden = ndarray::Array3::<f32>::from_shape_vec(
            (1, 4, 3),
            vec![
                1.0, 2.0, 3.0,
                4.0, 5.0, 6.0,
                99.0, 99.0, 99.0,
                99.0, 99.0, 99.0,
            ],
        )
        .unwrap()
        .into_dyn();
        let mask = ndarray::Array2::<i64>::from_shape_vec((1, 4), vec![1, 1, 0, 0]).unwrap();

        let pooled = mean_pool(&last_hidden, &mask).unwrap();
        assert_eq!(pooled.shape(), &[1, 3]);
        let row: Vec<f32> = pooled.row(0).to_vec();
        assert!((row[0] - 2.5).abs() < 1e-6, "got {row:?}");
        assert!((row[1] - 3.5).abs() < 1e-6, "got {row:?}");
        assert!((row[2] - 4.5).abs() < 1e-6, "got {row:?}");
    }

    /// All-padding row falls back to first-token (no NaN). Defensive path.
    #[test]
    fn mean_pool_all_padding_uses_first_token() {
        let last_hidden = ndarray::Array3::<f32>::from_shape_vec(
            (1, 2, 2),
            vec![7.0, 8.0, 99.0, 99.0],
        )
        .unwrap()
        .into_dyn();
        let mask = ndarray::Array2::<i64>::from_shape_vec((1, 2), vec![0, 0]).unwrap();
        let pooled = mean_pool(&last_hidden, &mask).unwrap();
        let row: Vec<f32> = pooled.row(0).to_vec();
        assert_eq!(row, vec![7.0, 8.0]);
    }

    /// Multi-batch: each row pools independently against its own mask.
    #[test]
    fn mean_pool_multi_batch_independent_masks() {
        let last_hidden = ndarray::Array3::<f32>::from_shape_vec(
            (2, 3, 1),
            vec![
                1.0, 2.0, 3.0,    // batch 0
                10.0, 20.0, 30.0, // batch 1
            ],
        )
        .unwrap()
        .into_dyn();
        // batch 0: all real → mean = 2.0
        // batch 1: only first → mean = 10.0
        let mask = ndarray::Array2::<i64>::from_shape_vec(
            (2, 3),
            vec![1, 1, 1, 1, 0, 0],
        )
        .unwrap();
        let pooled = mean_pool(&last_hidden, &mask).unwrap();
        assert!((pooled[[0, 0]] - 2.0).abs() < 1e-6);
        assert!((pooled[[1, 0]] - 10.0).abs() < 1e-6);
    }

    #[test]
    fn parse_pooling_round_trips() {
        assert_eq!(parse_pooling("cls").unwrap(), Pooling::Cls);
        assert_eq!(parse_pooling("mean").unwrap(), Pooling::Mean);
        assert!(parse_pooling("max").is_err());
    }
}
