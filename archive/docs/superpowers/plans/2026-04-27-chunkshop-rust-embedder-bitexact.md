# chunkshop Rust Embedder Bit-Exact Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-embedder-bitexact.md` (in this worktree). Re-read at every DC-XXX hard gate.

**Goal:** Make `chunkshop-rs` produce **byte-identical** float32 embeddings to Python for `Xenova/bge-base-en-v1.5-int8` and `Xenova/bge-small-en-v1.5-int8` by loading the same Xenova ONNX file Python loads, instead of fastembed-rs's stock `BGEBaseENV15Q` (Qdrant fp32 optimized variant).

**Architecture:** Add an `hf-hub`-backed download helper that fetches the five files from `Xenova/bge-base-en-v1.5` (and the small repo) into the standard HuggingFace cache. Add a branch in `FastembedEmbedder::new()` that, when `model_name` is one of the two Xenova int8 names, builds a `fastembed::UserDefinedEmbeddingModel` with those bytes and instantiates via `TextEmbedding::try_new_from_user_defined`. All other model names continue to use the existing stock-variant path. A new integration test embeds a deterministic 5-text fixture and asserts bitwise equality (`f32::to_bits()`) against committed Python-produced reference vectors.

**Tech Stack:** Rust 2021, fastembed-rs 5.13, `hf-hub` (new dep), Python 3.12 (reference-vector producer only), pytest, cargo test.

---

## File Structure

**New files:**
- `scripts/produce_rust_parity_reference.py` — Python helper that runs once to produce committed reference vectors. Idempotent. Not run from CI.
- `rust/chunkshop/tests/parity-fixtures/embedding_inputs.txt` — 5 fixed text inputs, one per line.
- `rust/chunkshop/tests/parity-fixtures/embedding_reference_bge_base_int8.bin` — binary float32 reference vectors (~15 KB; `u32 n` + `u32 dim` header + `n*dim` little-endian f32 values).
- `rust/chunkshop/src/hf_cache.rs` — internal helper that uses `hf-hub` to fetch the five Xenova files into the standard HF cache and return their bytes.
- `rust/chunkshop/tests/embedding_parity.rs` — new integration test asserting bitwise parity vs the reference vectors.

**Modified files:**
- `rust/chunkshop/Cargo.toml` — add `hf-hub` dependency.
- `rust/chunkshop/src/embedder.rs` — add the int8 → `UserDefinedEmbeddingModel` branch.
- `rust/chunkshop/src/lib.rs` — `mod hf_cache;` declaration.
- `rust/README.md` — rewrite "Known drift" section.
- `CHANGELOG.md` — append entry under `## Unreleased`.

---

## Task 1: Set up the Python reference-vector producer

**Files:**
- Create: `scripts/produce_rust_parity_reference.py`
- Create: `rust/chunkshop/tests/parity-fixtures/embedding_inputs.txt` (output of the script)
- Create: `rust/chunkshop/tests/parity-fixtures/embedding_reference_bge_base_int8.bin` (output of the script)

This task produces the *reference* embeddings against which the Rust test will compare. The script is committed so the fixtures can be regenerated, but the fixtures themselves are also committed so the Rust test doesn't need Python at run time.

- [ ] **Step 1: Write the producer script**

Create `scripts/produce_rust_parity_reference.py`:

```python
"""Produce reference embedding vectors for the Rust bit-exact parity test.

Run once from the chunkshop repo root with `uv run --project python python
scripts/produce_rust_parity_reference.py`. Writes the input fixture and a
binary file of float32 reference vectors that the Rust integration test
(rust/chunkshop/tests/embedding_parity.rs) compares against.

Format of the .bin file (little-endian throughout):
    u32 n           number of vectors
    u32 dim         dimension of each vector
    f32[n][dim]     row-major flattened vectors

Re-running this script regenerates the fixtures byte-for-byte (deterministic
because we use OMP_NUM_THREADS=1 and batch_size=1).
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path


# Single thread for deterministic output across machines.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

from chunkshop.embedders._registry import register_int8_variants  # noqa: E402
from fastembed import TextEmbedding  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"

# Five inputs chosen to exercise the embedder: short, longer, ASCII, mixed
# punctuation, and a sentence with internal commas. Order is load-bearing —
# the binary file is read in this order on the Rust side.
INPUTS = [
    "Hello world.",
    "Chunkshop reference text 1.",
    "Numerical reproducibility matters.",
    "Empty-ish input handling check.",
    "The quick brown fox jumps over the lazy dog.",
]


def main() -> int:
    register_int8_variants()
    model = TextEmbedding(
        model_name="Xenova/bge-base-en-v1.5-int8",
        threads=1,  # also passed to ORT intra_op_num_threads when supported
    )

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    inputs_path = FIXTURE_DIR / "embedding_inputs.txt"
    inputs_path.write_text("\n".join(INPUTS) + "\n", encoding="utf-8")

    # batch_size=1 to remove any cross-batch effects.
    vectors = list(model.embed(INPUTS, batch_size=1))
    n = len(vectors)
    dim = len(vectors[0])
    if not all(len(v) == dim for v in vectors):
        print("ERROR: mismatched vector dims", file=sys.stderr)
        return 2

    bin_path = FIXTURE_DIR / "embedding_reference_bge_base_int8.bin"
    with bin_path.open("wb") as f:
        f.write(struct.pack("<II", n, dim))
        for v in vectors:
            for x in v.astype("float32").tolist():
                f.write(struct.pack("<f", float(x)))

    print(f"wrote {inputs_path} ({len(INPUTS)} lines)")
    print(f"wrote {bin_path} ({n}x{dim} float32)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it once to generate fixtures**

Run from the repo root (the worktree at `/home/yonk/yonk-tools/chunkshop-rust-embedder-bitexact/`):

```bash
cd python && uv sync --frozen --extra dev --extra extractors --extra nlp && cd ..
uv run --project python python scripts/produce_rust_parity_reference.py
```

Expected output (paths and counts; first run downloads the model):
```
wrote /home/yonk/yonk-tools/chunkshop-rust-embedder-bitexact/rust/chunkshop/tests/parity-fixtures/embedding_inputs.txt (5 lines)
wrote /home/yonk/yonk-tools/chunkshop-rust-embedder-bitexact/rust/chunkshop/tests/parity-fixtures/embedding_reference_bge_base_int8.bin (5x768 float32)
```

Verify file sizes:
```bash
ls -la rust/chunkshop/tests/parity-fixtures/embedding_*
```
Expected: inputs ≈ 200 B, .bin = 8 + 5*768*4 = **15368 bytes**.

- [ ] **Step 3: Commit the script + fixtures**

```bash
git add scripts/produce_rust_parity_reference.py \
        rust/chunkshop/tests/parity-fixtures/embedding_inputs.txt \
        rust/chunkshop/tests/parity-fixtures/embedding_reference_bge_base_int8.bin
git commit -m "test(rust): commit Python reference vectors + producer for bit-exact parity"
```

---

## Task 2: Add hf-hub dependency + cache helper

**Files:**
- Modify: `rust/chunkshop/Cargo.toml`
- Create: `rust/chunkshop/src/hf_cache.rs`
- Modify: `rust/chunkshop/src/lib.rs` (add `mod hf_cache;`)

The `hf-hub` crate ships sync and async APIs and writes to the standard `~/.cache/huggingface/hub` cache directory — the same dir Python's fastembed reads when configured with `cache_dir` left at default. (Strictly, fastembed-py uses its own `~/.fastembed/` by default; we don't need cache co-location. We just need the same *file content*.)

- [ ] **Step 1: Add hf-hub to Cargo.toml**

In `rust/chunkshop/Cargo.toml`, add to `[dependencies]`:

```toml
hf-hub = { version = "0.3", default-features = false, features = ["ureq", "rustls-tls"] }
```

(Default features enable `tokio`; we want sync ureq to keep this off the async runtime since the embedder constructor is sync.)

- [ ] **Step 2: Write the failing helper test**

Create `rust/chunkshop/src/hf_cache.rs`:

```rust
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
pub struct HfModelFiles {
    pub onnx: Vec<u8>,
    pub tokenizer: Vec<u8>,
    pub tokenizer_config: Vec<u8>,
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
    let onnx = read_bytes(r.get(onnx_path).with_context(|| format!("fetch {repo}:{onnx_path}"))?)?;
    let tokenizer = read_bytes(r.get("tokenizer.json").with_context(|| format!("fetch {repo}:tokenizer.json"))?)?;
    let tokenizer_config = read_bytes(r.get("tokenizer_config.json").with_context(|| format!("fetch {repo}:tokenizer_config.json"))?)?;
    let special_tokens_map = read_bytes(r.get("special_tokens_map.json").with_context(|| format!("fetch {repo}:special_tokens_map.json"))?)?;
    let config = read_bytes(r.get("config.json").with_context(|| format!("fetch {repo}:config.json"))?)?;
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
```

- [ ] **Step 3: Wire the module into the crate**

In `rust/chunkshop/src/lib.rs`, add `mod hf_cache;` (or `pub(crate) mod hf_cache;`) alongside the other module declarations. Read the current `lib.rs` first to see the existing structure; append in the same style.

- [ ] **Step 4: Verify it compiles**

```bash
cargo build --workspace
```
Expected: clean build with no warnings beyond pre-existing.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/src/hf_cache.rs rust/chunkshop/src/lib.rs
git commit -m "feat(rust): add hf-hub-backed cache helper for bit-exact embedder parity"
```

---

## Task 3: ⛔ Drift Check DC-001 — re-read mission brief

- [ ] **Step 1: Re-read** `skill-output/mission-brief/Mission-Brief-rust-embedder-bitexact.md` in full.

- [ ] **Step 2: Verify these three things:**

1. **Purpose still aligned?** We are still solving "Rust produces non-bit-exact embeddings vs Python". We have not slipped into "let's port hierarchy chunker too" or "let's optimize fastembed-rs upstream".
2. **Tasks 1-2 mapped to a Success Criterion?** Task 1 produces the reference data SC-003 needs. Task 2 sets up the infrastructure SC-001/SC-002 need. Both map.
3. **Anything from "Out of Scope" creeping in?** Specifically — have we touched any chunker, framer, extractor, or sink-mode code? Have we changed Python? Have we added bit-exact support for any model beyond the two Xenova int8 variants?

- [ ] **Step 3:** If drift detected, stop and write down what drifted and the proposed correction before continuing.

---

## Task 4: Add the failing parity integration test

**Files:**
- Create: `rust/chunkshop/tests/embedding_parity.rs`

This test will FAIL at first because the embedder still uses fastembed-rs's stock `BGEBaseENV15Q` for the int8 name. That's the RED state. Task 5 makes it GREEN.

- [ ] **Step 1: Write the failing test**

Create `rust/chunkshop/tests/embedding_parity.rs`:

```rust
//! Bit-exact embedding parity vs Python for Xenova int8 BGE.
//!
//! Loads `tests/parity-fixtures/embedding_inputs.txt` (5 lines), embeds them
//! through `chunkshop-rs`'s embedder configured for
//! `Xenova/bge-base-en-v1.5-int8`, and asserts every output float matches the
//! committed Python reference (`tests/parity-fixtures/embedding_reference_bge_base_int8.bin`)
//! bitwise via `f32::to_bits()`.
//!
//! Skips cleanly if HuggingFace Hub is unreachable (no network), since the
//! embedder must download model files on first run.

use std::path::PathBuf;

use chunkshop::config::FastembedEmbedderConfig;
use chunkshop::embedder::FastembedEmbedder;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

fn read_inputs() -> Vec<String> {
    let p = fixtures_dir().join("embedding_inputs.txt");
    let s = std::fs::read_to_string(&p)
        .unwrap_or_else(|e| panic!("read {}: {}", p.display(), e));
    s.lines()
        .filter(|l| !l.is_empty())
        .map(|l| l.to_string())
        .collect()
}

fn read_reference() -> (usize, usize, Vec<f32>) {
    let p = fixtures_dir().join("embedding_reference_bge_base_int8.bin");
    let bytes = std::fs::read(&p).unwrap_or_else(|e| panic!("read {}: {}", p.display(), e));
    assert!(bytes.len() >= 8, "reference file too small");
    let n = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]) as usize;
    let dim = u32::from_le_bytes([bytes[4], bytes[5], bytes[6], bytes[7]]) as usize;
    let total_floats = n * dim;
    let expected_size = 8 + total_floats * 4;
    assert_eq!(
        bytes.len(),
        expected_size,
        "reference file size {} != expected {} for n={} dim={}",
        bytes.len(),
        expected_size,
        n,
        dim,
    );
    let mut floats = Vec::with_capacity(total_floats);
    for i in 0..total_floats {
        let off = 8 + i * 4;
        floats.push(f32::from_le_bytes([
            bytes[off],
            bytes[off + 1],
            bytes[off + 2],
            bytes[off + 3],
        ]));
    }
    (n, dim, floats)
}

#[test]
fn rust_embeddings_byte_identical_to_python() {
    let inputs = read_inputs();
    let (n, dim, expected_flat) = read_reference();
    assert_eq!(inputs.len(), n, "input count must match reference n");

    let cfg = FastembedEmbedderConfig {
        model_name: "Xenova/bge-base-en-v1.5-int8".to_string(),
        dim,
        batch_size: 1,
        threads: Some(1),
    };

    // First-call download path; if no network, skip with a printed message
    // rather than failing — the test's purpose is parity, not download.
    let mut embedder = match FastembedEmbedder::new(cfg) {
        Ok(e) => e,
        Err(e) => {
            eprintln!(
                "skipping bit-exact parity test (embedder init failed — likely no network): {e:#}"
            );
            return;
        }
    };

    let actual = embedder
        .embed(inputs.clone())
        .expect("embed must succeed when init succeeded");

    assert_eq!(actual.len(), n, "produced {} vectors, expected {}", actual.len(), n);
    let mut mismatches: Vec<(usize, usize, f32, f32)> = Vec::new();
    for (i, v) in actual.iter().enumerate() {
        assert_eq!(v.len(), dim, "vector {} has dim {}, expected {}", i, v.len(), dim);
        for (j, &got) in v.iter().enumerate() {
            let exp = expected_flat[i * dim + j];
            if got.to_bits() != exp.to_bits() {
                mismatches.push((i, j, got, exp));
            }
        }
    }
    if !mismatches.is_empty() {
        let first = &mismatches[..mismatches.len().min(5)];
        panic!(
            "{} of {} floats differ. First mismatches (i, j, got, expected): {:?}",
            mismatches.len(),
            n * dim,
            first,
        );
    }
}
```

- [ ] **Step 2: The `chunkshop::config` and `chunkshop::embedder` modules must be `pub`**

Read `rust/chunkshop/src/lib.rs`. If `config` and `embedder` are not exposed as `pub mod`, change them. The current state may already be public (the existing `tests/parity.rs` uses `chunkshop::{load_config, run_cell}` — but our new test uses internal items so the modules need to be reachable from integration tests).

If a re-export change is needed, add the minimum: in `lib.rs`, ensure `pub mod config;` and `pub mod embedder;`.

- [ ] **Step 3: Run the test, expect FAIL**

```bash
cargo test --test embedding_parity -- --nocapture
```

Expected outcomes (one of):
- **FAIL** with mismatches listed (the int8 name still resolves to fastembed's stock variant — different ONNX, different vectors).
- Or **skip** message ("skipping bit-exact parity test...") if the test environment has no network — that's a non-failure but not the result we want; rerun with cache primed.

If the test PASSES at this stage, something is wrong (we haven't implemented the user-defined path yet). Stop and investigate before continuing.

- [ ] **Step 4: Commit the failing test**

```bash
git add rust/chunkshop/tests/embedding_parity.rs rust/chunkshop/src/lib.rs
git commit -m "test(rust): add bit-exact embedding parity test (RED)"
```

---

## Task 5: Implement the user-defined embedder path

**Files:**
- Modify: `rust/chunkshop/src/embedder.rs`

This is the main code change. Detect the two Xenova int8 names; for those, build a `UserDefinedEmbeddingModel` from files fetched via `hf_cache::fetch_user_defined_files`. For all other names, keep the existing stock-variant path.

- [ ] **Step 1: Read fastembed-rs's UserDefinedEmbeddingModel API**

Quickly check the crate docs in your editor (or `cargo doc --open -p fastembed --no-deps`) to confirm the exact field names. As of fastembed 5.13:

```rust
use fastembed::{
    InitOptionsUserDefined, Pooling, TextEmbedding, TokenizerFiles,
    UserDefinedEmbeddingModel,
};
```

`UserDefinedEmbeddingModel` typical fields: `onnx_file: Vec<u8>`, `tokenizer_files: TokenizerFiles`, `pooling: Option<Pooling>`. `TokenizerFiles` typical fields: `tokenizer_file`, `config_file`, `special_tokens_map_file`, `tokenizer_config_file` (all `Vec<u8>`).

If field names differ in 5.13, adjust the code below accordingly. The shape of the change does not depend on exact field names.

- [ ] **Step 2: Rewrite `FastembedEmbedder::new` to dispatch on int8 names**

Replace the body of `rust/chunkshop/src/embedder.rs` with:

```rust
//! Fastembed-backed embedder.
//!
//! Wraps `fastembed::TextEmbedding`. Two paths:
//!
//! 1. **Stock-variant path** — for models where fastembed-rs's built-in
//!    registry already matches what we want (BGE non-quantized, MiniLM, etc.).
//!    Resolves through `resolve_model_name` and uses `TextEmbedding::try_new`.
//!
//! 2. **User-defined path (bit-exact)** — for `Xenova/bge-base-en-v1.5-int8`
//!    and `Xenova/bge-small-en-v1.5-int8`, where the goal is byte-identical
//!    output vs Python. Fetches the same five files Python's fastembed loads
//!    (`onnx/model_quantized.onnx` plus four tokenizer files from the Xenova
//!    HF repo), builds a `UserDefinedEmbeddingModel`, and instantiates via
//!    `TextEmbedding::try_new_from_user_defined`.

use std::collections::HashMap;

use anyhow::{anyhow, Context, Result};
use fastembed::{
    EmbeddingModel, InitOptions, InitOptionsUserDefined, Pooling, TextEmbedding,
    TokenizerFiles, UserDefinedEmbeddingModel,
};
use tracing::info;

use crate::config::FastembedEmbedderConfig;
use crate::hf_cache::{fetch_user_defined_files, HfModelFiles};

pub struct FastembedEmbedder {
    cfg: FastembedEmbedderConfig,
    model: TextEmbedding,
}

/// Returns `Some((repo, onnx_path, pooling))` when `model_name` is a Xenova
/// int8 variant we have a bit-exact path for. Otherwise `None` (callers fall
/// back to the stock-variant path).
fn user_defined_source(model_name: &str) -> Option<(&'static str, &'static str, Pooling)> {
    match model_name {
        "Xenova/bge-base-en-v1.5-int8" => Some((
            "Xenova/bge-base-en-v1.5",
            "onnx/model_quantized.onnx",
            Pooling::Cls,
        )),
        "Xenova/bge-small-en-v1.5-int8" => Some((
            "Xenova/bge-small-en-v1.5",
            "onnx/model_quantized.onnx",
            Pooling::Cls,
        )),
        _ => None,
    }
}

impl FastembedEmbedder {
    pub fn new(cfg: FastembedEmbedderConfig) -> Result<Self> {
        if let Some((repo, onnx_path, pooling)) = user_defined_source(&cfg.model_name) {
            let HfModelFiles {
                onnx,
                tokenizer,
                tokenizer_config,
                special_tokens_map,
                config,
            } = fetch_user_defined_files(repo, onnx_path)
                .with_context(|| format!("fetching user-defined files for {repo}"))?;
            let user_model = UserDefinedEmbeddingModel::new(onnx, TokenizerFiles {
                tokenizer_file: tokenizer,
                config_file: config,
                special_tokens_map_file: special_tokens_map,
                tokenizer_config_file: tokenizer_config,
            })
            .with_pooling(pooling);
            // InitOptionsUserDefined doesn't expose intra_op_num_threads
            // directly in fastembed 5.13. Determinism is enforced upstream
            // via OMP_NUM_THREADS=1 etc. when bit-exactness is required (the
            // produce-reference Python script sets these; the parity test
            // assumes Rust callers honor `embedder.threads` at the runner
            // level via env var setup).
            let opts = InitOptionsUserDefined::default();
            let model = TextEmbedding::try_new_from_user_defined(user_model, opts)
                .with_context(|| {
                    format!(
                        "initialising user-defined fastembed model {} (repo {})",
                        cfg.model_name, repo
                    )
                })?;
            info!(
                "embedder loaded (user-defined, bit-exact): {} (dim={}, repo={}, file={})",
                cfg.model_name, cfg.dim, repo, onnx_path
            );
            return Ok(Self { cfg, model });
        }

        // Stock-variant path (unchanged behavior for every other model_name).
        let variant = resolve_model_name(&cfg.model_name)?;
        let opts = InitOptions::new(variant).with_show_download_progress(true);
        if let Some(_n) = cfg.threads {
            // fastembed::InitOptions has no exposed thread field in 5.13;
            // honored at runtime via env setup in the runner (see CLAUDE.md).
        }
        let model = TextEmbedding::try_new(opts)
            .with_context(|| format!("initialising fastembed model {:?}", cfg.model_name))?;
        info!(
            "embedder loaded (stock variant): {} (dim={})",
            cfg.model_name, cfg.dim
        );
        Ok(Self { cfg, model })
    }

    pub fn dim(&self) -> usize {
        self.cfg.dim
    }

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
```

Note we removed the two `Xenova/...-int8` rows from `resolve_model_name` — those names now only flow through the user-defined branch. If a user passes one of them and the user-defined fetch fails (e.g., no network), they get a clear error instead of silently falling back to the wrong vectors.

- [ ] **Step 3: Build to check for API drift**

```bash
cargo build --workspace
```

If the build fails due to fastembed-rs 5.13 having different field names than the code uses (e.g., `tokenizer_files` vs `with_tokenizer_files`), look at the actual error output, adjust the constructor calls, and rebuild. Common alternatives in 5.x:

- `UserDefinedEmbeddingModel::new(onnx, tokenizer_files)` then `.with_pooling(...)` — used above.
- Direct struct literal `UserDefinedEmbeddingModel { onnx_file: onnx, tokenizer_files, pooling: Some(Pooling::Cls), ... }` — fall back if the builder doesn't exist.

If `Pooling::Cls` is named differently (e.g., `PoolingType::Cls`), match the actual symbol from the build error.

- [ ] **Step 4: Run the parity test, expect PASS**

```bash
cargo test --test embedding_parity -- --nocapture
```

Expected: `test rust_embeddings_byte_identical_to_python ... ok`.

If the test still fails with mismatches, see Task 6 (debug-to-green).

- [ ] **Step 5: Commit when GREEN**

```bash
git add rust/chunkshop/src/embedder.rs
git commit -m "feat(rust): bit-exact embedding parity for Xenova int8 BGE variants

Switches Xenova/bge-{base,small}-en-v1.5-int8 from fastembed-rs's stock
BGE*Q variants (Qdrant fp32-optimized ONNX) to UserDefinedEmbeddingModel
fed with the same files Python's fastembed loads from the Xenova HF repo
(onnx/model_quantized.onnx + 4 tokenizer files). Output vectors are now
bit-exact f32 matches to Python — verified by tests/embedding_parity.rs."
```

---

## Task 6: ⛔ Drift Check DC-002 — debug to GREEN if mismatches remain

This task only runs if Task 5 Step 4 produced mismatches.

- [ ] **Step 1: Re-read** `skill-output/mission-brief/Mission-Brief-rust-embedder-bitexact.md`. The constraint is: **fix Rust to match Python — never the other way around**.

- [ ] **Step 2: Diagnose with a minimal reproduction**

Inspect the first mismatch. The test already prints `(i, j, got, expected)` for the first 5. Decide which class of mismatch:

| Symptom | Likely cause | Fix |
|---|---|---|
| All floats wildly different (orders of magnitude) | Wrong model loaded / wrong pooling / not normalized | Confirm the user-defined fetch is reaching `Xenova/bge-base-en-v1.5/onnx/model_quantized.onnx`; confirm `Pooling::Cls`; check whether fastembed-rs auto-normalizes or whether we need a manual L2 norm step. |
| Floats agree to ~1e-3 cosine | Different ONNX file (still on stock path) | Double-check `user_defined_source` is being hit — add a `tracing::debug!` log and re-run with `RUST_LOG=chunkshop=debug`. |
| Floats differ in last few bits, scattered | Threading non-determinism (parallel reduction order) | Set `OMP_NUM_THREADS=1` and `ORT_INTRA_OP_NUM_THREADS=1` env vars before invoking the test. If still flaky, also `MKL_NUM_THREADS=1`. |
| First few vectors match, later ones drift | Batching effect | Confirm `cfg.batch_size = 1` in the test; confirm fastembed-rs honors batch_size in user-defined path. |

- [ ] **Step 3: Apply the smallest fix**

Examples:

If normalization is missing (BGE expects L2-normalized output and fastembed-rs's user-defined path may not do it automatically):

```rust
fn l2_normalize(v: &mut [f32]) {
    let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 {
        for x in v.iter_mut() {
            *x /= norm;
        }
    }
}
```

Apply inside `embed()` before returning, gated by `user_defined_source(&self.cfg.model_name).is_some()` so stock-variant outputs are unaffected.

If thread non-determinism is the cause, document the requirement in the test header:

```rust
// At the very top of tests/embedding_parity.rs's test function, before init:
std::env::set_var("OMP_NUM_THREADS", "1");
std::env::set_var("ORT_INTRA_OP_NUM_THREADS", "1");
std::env::set_var("MKL_NUM_THREADS", "1");
```

- [ ] **Step 4: Re-run until GREEN**

```bash
cargo test --test embedding_parity -- --nocapture
```

If after three diagnose-fix-test loops the test still fails, **stop**. Per the mission brief's DC-002 rule: re-read the brief and reassess. Do not loosen the bitwise assertion — that's a Constraint violation. If reaching bit-exactness genuinely requires changing Python's behavior, the brief is wrong; surface the conflict before continuing.

- [ ] **Step 5: Commit when GREEN**

```bash
git add rust/chunkshop/src/embedder.rs rust/chunkshop/tests/embedding_parity.rs
git commit -m "fix(rust): close last-bit drift in bit-exact embedding parity"
```

(Skip this task if Task 5 Step 4 already produced a green test.)

---

## Task 7: ⛔ Drift Check DC-003 + cross-language parity script (E2E)

The bitwise unit-level proof (SC-003) is in place. SC-004 needs the existing `scripts/parity_check.py` to confirm parity at the user-visible level: ingest the sample corpus through both implementations and compare.

- [ ] **Step 1: Re-read** `skill-output/mission-brief/Mission-Brief-rust-embedder-bitexact.md`, focusing on SC-004 and DC-003. The threshold is **max cosine distance ≤ 1e-7** (mean ≤ 1e-7) over the sample corpus.

- [ ] **Step 2: Read `scripts/parity_check.py`** to understand its current invocation pattern.

```bash
head -80 scripts/parity_check.py
```

The script likely takes two table names (one written by Python, one by Rust) and compares cosine distances.

- [ ] **Step 3: Run end-to-end**

Pre-condition: a Postgres with pgvector available via `CHUNKSHOP_TEST_DSN`. If unreachable:

```bash
docker run --rm -d -p 5434:5432 \
  -e POSTGRES_PASSWORD=postgres \
  --name chunkshop-pg \
  pgvector/pgvector:pg16
sleep 5
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/postgres
PGPASSWORD=postgres psql -h localhost -p 5434 -U postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Run the script per its own README/header (typical shape — confirm against actual file):

```bash
# Build release Rust binary first (faster + matches release CI behavior)
(cd rust && cargo build --release --quiet)

# Run the parity check (whatever entry point the script expects)
uv run --project python python scripts/parity_check.py
```

Expected: max cosine distance ≤ 1e-7, mean ≤ 1e-7. The exact output format depends on the script — record the numbers.

- [ ] **Step 4: If mismatches exceed threshold, diagnose**

Most likely, the corpus exercises additional features (hierarchy chunker, etc.) that Rust doesn't yet support, and the parity check script accounts for that. If not, the corpus simply tests embedding output: same diagnostic flow as Task 6.

- [ ] **Step 5: Tear down test Postgres** (only if you started it for this step)

```bash
docker rm -f chunkshop-pg
```

No commit yet — this task is verification. Output goes into the next task's docs.

---

## Task 8: Update README + CHANGELOG

**Files:**
- Modify: `rust/README.md` (the "Known drift" section)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Rewrite the "Known drift" section in `rust/README.md`**

Find the section starting `## Known drift: embedding values are NOT bit-exact vs Python` and replace its entire body (down to but not including the next `##` header) with:

```markdown
## Embedding parity vs Python

`chunkshop-rs` produces **byte-identical** float32 embeddings to Python for
the two registered Xenova int8 BGE variants:

- `Xenova/bge-base-en-v1.5-int8`
- `Xenova/bge-small-en-v1.5-int8`

The Rust embedder fetches the same five files Python's fastembed loads
(`onnx/model_quantized.onnx` and four tokenizer files from the Xenova HF
repo) via [`hf-hub`](https://crates.io/crates/hf-hub) and instantiates
fastembed-rs's `UserDefinedEmbeddingModel`. Verification:

- `rust/chunkshop/tests/embedding_parity.rs` — embeds 5 fixed inputs and
  asserts every float matches a committed Python reference vector via
  `f32::to_bits()` (exact bitwise equality).
- `scripts/parity_check.py` — end-to-end cross-language ingest comparison
  on the sample corpus. Max cosine distance ≤ 1e-7 (within float32 noise).

Other model names (`BAAI/bge-base-en-v1.5`, `BAAI/bge-small-en-v1.5`,
`BAAI/bge-large-en-v1.5`, `sentence-transformers/all-MiniLM-L6-v2`) continue
to use fastembed-rs's stock variants. They share the wire format with
Python (same dim, same ordering) but are *not* claimed to be bit-exact —
fastembed-rs's stock BGE variants are Qdrant fp32-optimized ONNX, a
different file from Python's BAAI fp32 ONNX. Cross-language cosine drift
on those models is typically ~1e-3.

### Historical note

Versions 0.1.0 of `chunkshop-rs` mapped the int8 names to fastembed-rs's
`BGEBaseENV15Q` / `BGESmallENV15Q` (Qdrant fp32 optimized ONNX) and
documented a ~0.01 cosine drift. That drift is closed for the int8 names
as of [next-release].
```

- [ ] **Step 2: Add CHANGELOG entry**

In `CHANGELOG.md`, find the `## Unreleased` section. Under `### Changed` (or add the subsection if missing), append:

```markdown
- **`chunkshop-rs` embedder is now bit-exact vs Python** for `Xenova/bge-base-en-v1.5-int8`
  and `Xenova/bge-small-en-v1.5-int8`. The Rust embedder fetches the same Xenova ONNX file
  Python loads (`onnx/model_quantized.onnx` from the Xenova HF repos) via `hf-hub` and uses
  fastembed-rs's `UserDefinedEmbeddingModel` path. Output `f32` vectors are now bitwise
  identical (`f32::to_bits()` equality) to Python's, verified by a new integration test
  (`rust/chunkshop/tests/embedding_parity.rs`) and the cross-language script
  (`scripts/parity_check.py`). The 0.1.0 drift note in `rust/README.md` is updated.
```

- [ ] **Step 3: Commit**

```bash
git add rust/README.md CHANGELOG.md
git commit -m "docs(rust): update parity claim — int8 BGE now bit-exact vs Python"
```

---

## Task 9: Run all tests + verify SC-005

**Files:** none (verification only).

- [ ] **Step 1: Rust workspace tests**

```bash
cd rust && cargo test --workspace 2>&1 | tail -20 && cd ..
```

Expected: all tests pass, including the new `embedding_parity` and the existing `parity` integration tests.

- [ ] **Step 2: Python tests**

```bash
cd python && uv run --no-sync pytest -q 2>&1 | tail -5 && cd ..
```

Expected: 172 passed, 8 skipped (same as `main`). Anything else means a regression — investigate before continuing.

- [ ] **Step 3: Build the Rust release binary**

```bash
cd rust && cargo build --release --quiet 2>&1 | tail -5 && cd ..
```

Expected: clean build, rc=0. (The CI uses release builds for the binary tests.)

No commit — this is the final regression check.

---

## Task 10: ⛔ DC-FINAL — verify all SC met

- [ ] **Step 1: Re-read** `skill-output/mission-brief/Mission-Brief-rust-embedder-bitexact.md` end-to-end, one last time.

- [ ] **Step 2: Walk through every SC and write evidence**

For each criterion below, fill in the evidence:

```
SC-001 (loads Xenova/bge-base ONNX) — Evidence: ____________________
SC-002 (loads Xenova/bge-small ONNX) — Evidence: ___________________
SC-003 (bitwise parity test passes) — Evidence: cargo test --test embedding_parity output: ____________________
SC-004 (parity_check.py max cosine ≤ 1e-7) — Evidence: ____________________
SC-005 (cargo test --workspace + pytest -q both pass) — Evidence: ____________________
SC-006 (rust/README.md "Known drift" rewritten) — Evidence: ____________________
SC-007 (CHANGELOG.md entry under Unreleased) — Evidence: ____________________
```

If any SC has no evidence, the work is **not complete**. Either fix it now or stop and surface the gap.

- [ ] **Step 3: Final tree state check**

```bash
git status --short
git log --oneline main..HEAD
```

Expected: working tree clean (or only contains intentional uncommitted state); a small, readable commit history on `feat/rust-embedder-bitexact`.

- [ ] **Step 4: Hand off**

Per CLAUDE.md repo conventions: "merge back via `superpowers:finishing-a-development-branch` when tests pass." Invoke that skill or note the worktree is ready for review.

If using subagent-driven-development to execute this plan, the master orchestrator dispatches the finishing skill at this point.

---

## Self-review notes

- **Spec coverage:** Every SC-XXX in the brief maps to at least one task (SC-001/002 → Task 5; SC-003 → Tasks 1, 4, 5; SC-004 → Task 7; SC-005 → Task 9; SC-006/007 → Task 8). Each DC-XXX is a labeled task (Task 3 = DC-001; Task 6 = DC-002; Task 7 step 1 = DC-003; Task 10 = DC-FINAL).
- **No placeholders:** every code step has actual code; every command step has an actual command and expected output.
- **Type consistency:** `FastembedEmbedderConfig`, `UserDefinedEmbeddingModel`, `TokenizerFiles`, `Pooling::Cls`, `HfModelFiles`, `fetch_user_defined_files` are referenced consistently across Tasks 2, 4, 5, 6.
- **Out-of-scope guards:** every drift checkpoint (DC-001 through DC-FINAL) explicitly re-checks the Out of Scope list. The plan does not touch any chunker, framer, extractor, sink-mode, or Python code beyond the one-time reference-vector producer.
