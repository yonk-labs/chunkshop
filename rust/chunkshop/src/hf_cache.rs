//! HuggingFace Hub file fetcher.
//!
//! Downloads the five files needed to instantiate a fastembed
//! `UserDefinedEmbeddingModel` — `onnx/model_quantized.onnx`, `tokenizer.json`,
//! `tokenizer_config.json`, `special_tokens_map.json`, `config.json` — into
//! the standard HF cache (`~/.cache/huggingface/hub`). Returns the bytes for
//! each file. Sync; uses `hf-hub`'s `ureq` backend.

use std::path::PathBuf;

use anyhow::{Context, Result};
use hf_hub::api::sync::Api;

/// Files needed to construct `fastembed::UserDefinedEmbeddingModel`.
///
/// `special_tokens_map` is fetched (because fastembed-py reads it) but
/// chunkshop-rs's hand-rolled embedder only needs `tokenizer.json` for
/// tokenization — the special-tokens table is already baked into the
/// tokenizer.json the BGE Xenova repos ship. The field is kept for
/// future-proofing and to keep the file-set parallel to fastembed-py.
pub struct HfModelFiles {
    pub onnx: Vec<u8>,
    pub tokenizer: Vec<u8>,
    pub tokenizer_config: Vec<u8>,
    #[allow(dead_code)]
    pub special_tokens_map: Vec<u8>,
    pub config: Vec<u8>,
}

/// Fetch the five files from the given HF repo. `onnx_path` is a repo-relative
/// path like `"onnx/model_quantized.onnx"`. The other four file names are
/// fixed: `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`,
/// `config.json`.
pub fn fetch_user_defined_files(repo: &str, onnx_path: &str) -> Result<HfModelFiles> {
    let api = Api::new().context("init hf-hub api")?;
    let r = api.model(repo.to_string());
    let onnx = read_bytes(
        r.get(onnx_path)
            .with_context(|| format!("fetch {repo}:{onnx_path}"))?,
    )?;
    let tokenizer = read_bytes(
        r.get("tokenizer.json")
            .with_context(|| format!("fetch {repo}:tokenizer.json"))?,
    )?;
    let tokenizer_config = read_bytes(
        r.get("tokenizer_config.json")
            .with_context(|| format!("fetch {repo}:tokenizer_config.json"))?,
    )?;
    let special_tokens_map = read_bytes(
        r.get("special_tokens_map.json")
            .with_context(|| format!("fetch {repo}:special_tokens_map.json"))?,
    )?;
    let config = read_bytes(
        r.get("config.json")
            .with_context(|| format!("fetch {repo}:config.json"))?,
    )?;
    Ok(HfModelFiles {
        onnx,
        tokenizer,
        tokenizer_config,
        special_tokens_map,
        config,
    })
}

fn read_bytes(p: PathBuf) -> Result<Vec<u8>> {
    std::fs::read(&p).with_context(|| format!("read cached file {}", p.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Compile-only sanity: the function signature exists and takes &str. We
    /// don't actually hit HF here — that would slow unit tests and require
    /// network. The integration test in `tests/embedding_parity.rs` exercises
    /// the network path.
    #[test]
    fn function_compiles() {
        let _ = fetch_user_defined_files;
    }
}
