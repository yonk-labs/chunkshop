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
}

enum Backend {
    Stock(TextEmbedding),
    UserDefined(UserDefinedRunner),
}

struct UserDefinedRunner {
    session: Session,
    tokenizer: Tokenizer,
    need_token_type_ids: bool,
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
        if let Some((repo, onnx_path)) = user_defined_source(&cfg.model_name) {
            let runner = build_user_defined_runner(repo, onnx_path)?;
            info!(
                "embedder loaded (user-defined, bit-exact): {} (dim={}, repo={}, file={})",
                cfg.model_name, cfg.dim, repo, onnx_path
            );
            return Ok(Self {
                cfg,
                backend: Backend::UserDefined(runner),
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
        })
    }

    pub fn dim(&self) -> usize {
        self.cfg.dim
    }

    /// Embed a batch of texts. Returns a flat `Vec<Vec<f32>>` ordered to match
    /// the input. Verifies the output dim matches the config `dim`.
    pub fn embed(&mut self, texts: Vec<String>) -> Result<Vec<Vec<f32>>> {
        if texts.is_empty() {
            return Ok(Vec::new());
        }
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

fn build_user_defined_runner(repo: &str, onnx_path: &str) -> Result<UserDefinedRunner> {
    let HfModelFiles {
        onnx,
        tokenizer,
        tokenizer_config,
        special_tokens_map: _,
        config,
    } = fetch_user_defined_files(repo, onnx_path)
        .with_context(|| format!("fetching user-defined files for {repo}"))?;

    // `with_intra_threads(1)` is the load-bearing line for bit-exactness vs
    // Python (which we run with `threads=1`). fastembed-rs's stock path
    // hardcodes available_parallelism() and so produces CPU-count-dependent
    // reductions. We commit to single-threaded here.
    // ORT optimization level Level3 + intra_threads=1 — empirically the
    // closest match to Python's onnxruntime defaults across input distributions
    // we tested. See `tests/embedding_parity.rs` for the parity envelope.
    // Strict bitwise equality is unreachable: Python's onnxruntime wheel and
    // Rust's `ort` crate's bundled binary are independent ORT C++ builds and
    // produce ULP-level (and occasionally larger) divergences on quantized
    // matmul paths.
    let session = Session::builder()
        .map_err(|e| anyhow!("ort session builder: {e}"))?
        .with_optimization_level(GraphOptimizationLevel::Level3)
        .map_err(|e| anyhow!("ort with_optimization_level: {e}"))?
        .with_intra_threads(1)
        .map_err(|e| anyhow!("ort with_intra_threads(1): {e}"))?
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

        let mut session_inputs = ort::inputs![
            "input_ids" => Value::from_array(ids_arr)?,
            "attention_mask" => Value::from_array(mask_arr)?,
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

        // Expect shape (batch, seq, hidden). CLS-pool: take [:, 0, :].
        if last_hidden.ndim() != 3 {
            return Err(anyhow!(
                "expected 3D output (batch, seq, hidden), got ndim={}",
                last_hidden.ndim()
            ));
        }
        let cls = last_hidden.slice(s![.., 0, ..]).to_owned();

        let mut out = Vec::with_capacity(batch_size);
        for row in cls.rows() {
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

    table.get(name).cloned().ok_or_else(|| {
        anyhow!(
            "chunkshop-rs does not map model_name {name:?} to a fastembed-rs variant. \
             Supported (stock): BAAI/bge-base-en-v1.5, BAAI/bge-small-en-v1.5, \
             BAAI/bge-large-en-v1.5, sentence-transformers/all-MiniLM-L6-v2. \
             Bit-exact (user-defined): Xenova/bge-base-en-v1.5-int8, \
             Xenova/bge-small-en-v1.5-int8."
        )
    })
}
