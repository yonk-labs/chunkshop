//! HuggingFace Hub file fetcher.
//!
//! Downloads the five files needed to instantiate a fastembed
//! `UserDefinedEmbeddingModel` — `onnx/model_quantized.onnx`, `tokenizer.json`,
//! `tokenizer_config.json`, `special_tokens_map.json`, `config.json` — into
//! the standard HF cache (`~/.cache/huggingface/hub`). Returns the bytes for
//! each file. Sync; uses `hf-hub`'s `ureq` backend.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use hf_hub::api::sync::{Api, ApiError};

/// How long to wait for another thread or process that holds a file's download
/// lock. hf-hub gives up after about 5 seconds, which is far less than a cold
/// model download takes, so concurrent first runs failed with
/// "Lock acquisition failed".
const LOCK_WAIT: Duration = Duration::from_secs(600);

/// Call `f` again while it fails only because the download lock is held.
/// Once the holder finishes, hf-hub's `get` finds the file in the cache.
fn retry_while_locked<T>(
    max_wait: Duration,
    mut f: impl FnMut() -> std::result::Result<T, ApiError>,
) -> std::result::Result<T, ApiError> {
    let start = Instant::now();
    loop {
        match f() {
            Err(ApiError::LockAcquisition(_)) if start.elapsed() < max_wait => {
                // hf-hub already slept ~5s inside its own lock attempt.
                std::thread::sleep(Duration::from_millis(250));
            }
            other => return other,
        }
    }
}

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
    let get = |name: &str| retry_while_locked(LOCK_WAIT, || r.get(name));
    let onnx = read_bytes(
        get(onnx_path)
            .with_context(|| format!("fetch {repo}:{onnx_path}"))?,
    )?;
    let tokenizer = read_bytes(
        get("tokenizer.json")
            .with_context(|| format!("fetch {repo}:tokenizer.json"))?,
    )?;
    let tokenizer_config = read_bytes(
        get("tokenizer_config.json")
            .with_context(|| format!("fetch {repo}:tokenizer_config.json"))?,
    )?;
    let special_tokens_map = read_bytes(
        get("special_tokens_map.json")
            .with_context(|| format!("fetch {repo}:special_tokens_map.json"))?,
    )?;
    let config = read_bytes(
        get("config.json")
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

    fn locked() -> ApiError {
        ApiError::LockAcquisition(PathBuf::from("x.lock"))
    }

    #[test]
    fn retries_until_the_lock_holder_finishes() {
        let mut calls = 0;
        let got = retry_while_locked(Duration::from_secs(30), || {
            calls += 1;
            if calls < 3 {
                Err(locked())
            } else {
                Ok("path")
            }
        });
        assert_eq!(got.unwrap(), "path");
        assert_eq!(calls, 3);
    }

    #[test]
    fn gives_up_after_max_wait_and_keeps_the_lock_error() {
        let mut calls = 0;
        let got: std::result::Result<(), ApiError> = retry_while_locked(Duration::ZERO, || {
            calls += 1;
            Err(locked())
        });
        assert!(matches!(got, Err(ApiError::LockAcquisition(_))));
        assert_eq!(calls, 1);
    }

    #[test]
    fn other_errors_are_returned_immediately() {
        let mut calls = 0;
        let got: std::result::Result<(), ApiError> =
            retry_while_locked(Duration::from_secs(30), || {
                calls += 1;
                Err(ApiError::IoError(std::io::Error::other("disk full")))
            });
        assert!(got.is_err());
        assert_eq!(calls, 1);
    }
}
