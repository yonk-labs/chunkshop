# chunkshop-rs (crate)

Rust crate for chunkshop. See `../README.md` for the top-level Rust port
overview and Python-vs-Rust feature parity table. This file documents
crate-level concerns specific to the `chunkshop` crate's Cargo features.

## Code-aware chunking

The `code-aware` feature enables symbol-aware chunking for source code via
[tree-sitter](https://crates.io/crates/tree-sitter). Each grammar is
opt-in to control binary size.

### Feature matrix

| Feature flag | Languages | Binary (MB) | Δ vs default |
|---|---|---|---|
| `default` (none) | — | 51.40 | (baseline) |
| `code-aware` (umbrella alone) | — | 51.42 | +0.02 |
| `code-aware-python` | Python | 52.14 | +0.74 |
| `code-aware-python,code-aware-java` | Python + Java (must-have set) | 52.54 | +1.14 |
| `code-aware-go` | Go (should-have, pending [chunkshop#40](https://github.com/yonk-labs/chunkshop/issues/40)) | not implemented | n/a |
| `code-aware-typescript` | TypeScript (should-have) | not implemented | n/a |
| `code-aware-javascript` | JavaScript (should-have) | not implemented | n/a |
| `code-aware-rust` | Rust (should-have) | not implemented | n/a |

Sizes captured with `cargo build --release --bin chunkshop-rs` on this
build host. Will drift over time — re-measure with the same matrix to
update.

The umbrella `code-aware` feature alone adds no grammar crates (the
+0.02 MB drift is build-noise from a fresh recompile, not real code) —
it's a marker for downstream crates that want to feature-detect "any
code-aware grammar is on". Real cost arrives with the per-language
features, which gate `tree-sitter`, `tree-sitter-tags`, and the
language grammar crate behind `dep:` syntax.

### Cross-port byte-equivalence

When the same source file is ingested via chunkshop-py and chunkshop-rs,
the resulting chunks share identical `fqn` and `node_id` metadata. This
is enforced by:

- **Rust proptest** at `rust/chunkshop/tests/cross_port_proptest.rs`
  (~1500 random cases per `cargo test`)
- **Python pytest** at `python/tests/chunkshop/test_rust_cross_port_parity.py`
  (46 curated vectors, invokes the `fqn-cli` Rust binary as subprocess)

Both gates run in CI on every PR.

### Syntax-error fallback

When tree-sitter returns an error-containing parse tree (mirroring Python's
`ast.parse → SyntaxError` check), or when the document's language can't be
detected from its extension, the chunker falls back to `sentence_aware` and
stamps `metadata.strategy = "symbol_aware_fallback"` with a
`fallback_reason` for observability.

### Should-have grammars status

`code-aware-go`, `code-aware-typescript`, `code-aware-javascript`, and
`code-aware-rust` feature flags exist in `Cargo.toml` and pull in their
respective grammar crates, but the per-language extractors are not yet
implemented. Pending [chunkshop#40](https://github.com/yonk-labs/chunkshop/issues/40)
(Python's tree-sitter migration for Go/TS/JS) — implementing them first in
Python keeps the cross-port equivalence contract intact.
