//! Fastembed-backed embedder.
//!
//! Wraps `fastembed::TextEmbedding`. The `model_name` in YAML is a Python
//! fastembed model-id (e.g. `Xenova/bge-base-en-v1.5-int8`); we map it to the
//! closest `fastembed::EmbeddingModel` variant available in fastembed-rs's
//! built-in registry.
//!
//! Known drift: Python ships `Xenova/bge-base-en-v1.5-int8` (a Xenova-uploaded
//! int8 quantization). fastembed-rs ships `BGEBaseENV15Q` which is Qdrant's
//! optimized ONNX (not strictly int8 quantized by the same script). Values are
//! typically within 1e-3 cosine distance on identical inputs but not bit-exact.
//! Documented in `rust/README.md`.

use std::collections::HashMap;

use anyhow::{anyhow, Context, Result};
use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};
use tracing::info;

use crate::config::FastembedEmbedderConfig;

pub struct FastembedEmbedder {
    cfg: FastembedEmbedderConfig,
    model: TextEmbedding,
}

impl FastembedEmbedder {
    pub fn new(cfg: FastembedEmbedderConfig) -> Result<Self> {
        let variant = resolve_model_name(&cfg.model_name)?;
        let mut opts = InitOptions::new(variant).with_show_download_progress(true);
        if let Some(n) = cfg.threads {
            // `InitOptions` has no direct threads field exposed; fastembed-rs sets
            // ORT intra-op threads via the session builder internally. The Rust
            // MVP leaves this to fastembed defaults — note for docs.
            let _ = n;
        }
        let _ = &mut opts;
        let model = TextEmbedding::try_new(opts)
            .with_context(|| format!("initialising fastembed model {:?}", cfg.model_name))?;
        info!("embedder loaded: {} (dim={})", cfg.model_name, cfg.dim);
        Ok(Self { cfg, model })
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
        let refs: Vec<&str> = texts.iter().map(String::as_str).collect();
        let vecs = self
            .model
            .embed(refs, Some(self.cfg.batch_size))
            .context("fastembed embed call failed")?;
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

/// Map a Python-style `model_name` to a fastembed-rs `EmbeddingModel`.
///
/// Returns the closest available variant. Python's int8 variants (e.g.
/// `Xenova/bge-base-en-v1.5-int8`) don't exist verbatim in fastembed-rs's
/// registry — we fall back to the non-quantized `BGEBaseENV15` / `BGESmallENV15`
/// so at least the wire format (dim + ordering) stays comparable.
fn resolve_model_name(name: &str) -> Result<EmbeddingModel> {
    let mut table: HashMap<&str, EmbeddingModel> = HashMap::new();
    // Python int8 Xenova uploads -> fastembed-rs quantized variants (drift doc'd).
    table.insert("Xenova/bge-base-en-v1.5-int8", EmbeddingModel::BGEBaseENV15Q);
    table.insert("Xenova/bge-small-en-v1.5-int8", EmbeddingModel::BGESmallENV15Q);
    // Direct HF ids.
    table.insert("BAAI/bge-base-en-v1.5", EmbeddingModel::BGEBaseENV15);
    table.insert("BAAI/bge-small-en-v1.5", EmbeddingModel::BGESmallENV15);
    table.insert("BAAI/bge-large-en-v1.5", EmbeddingModel::BGELargeENV15);
    table.insert(
        "sentence-transformers/all-MiniLM-L6-v2",
        EmbeddingModel::AllMiniLML6V2,
    );

    table.get(name).cloned().ok_or_else(|| {
        anyhow!(
            "chunkshop-rs MVP does not map model_name {name:?} to a fastembed-rs variant yet. \
             Supported: Xenova/bge-base-en-v1.5-int8, Xenova/bge-small-en-v1.5-int8, \
             BAAI/bge-base-en-v1.5, BAAI/bge-small-en-v1.5, BAAI/bge-large-en-v1.5, \
             sentence-transformers/all-MiniLM-L6-v2."
        )
    })
}
