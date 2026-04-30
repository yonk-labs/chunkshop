# BYO embedder — point at any HuggingFace ONNX from YAML alone

Adding a new embedding model to chunkshop used to mean editing source in
**both** `python/src/chunkshop/embedders/_registry.py` AND
`rust/chunkshop/src/embedder.rs`, then reinstalling Python and rebuilding
Rust. That ergonomics is fixed.

This sample demonstrates the YAML-only path: four lines of config, no code
edits, same YAML works in both languages.

## Files

| File | Role |
|---|---|
| [`byo.yaml`](byo.yaml) | The YAML, with the four BYO fields highlighted |
| [`run_demo.sh`](run_demo.sh) | Verifies end-to-end from both languages |

## The four BYO fields

```yaml
embedder:
  type: fastembed
  model_name: byo-demo-bge-small-fp32   # any unique label
  dim: 384                              # contract — must match runtime output

  hf_repo: Xenova/bge-small-en-v1.5     # NEW — where to fetch from
  onnx_path: onnx/model.onnx            # NEW — file inside the repo
  pooling: cls                          # NEW — "cls" or "mean", default "cls"
  # additional_files: [...]             # NEW (optional, defaults sane)
```

When `hf_repo` is set, both `chunkshop ingest` and `chunkshop-rs ingest`
fetch the model from HuggingFace at config-load time and use it directly.
No registry edit, no rebuild.

When `hf_repo` is **not** set (existing YAMLs), dispatch falls back to the
hardcoded registries — every existing config keeps working unchanged.

## Run the demo

```bash
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd /path/to/chunkshop                 # repo root
cd python && uv sync --extra dev && cd ..
(cd rust && cargo build --release)

bash docs/samples/embedder-byo/run_demo.sh
```

Verified output:

```
==== step 1: Python ingest (BYO embedder via YAML) ====
[python] 12 chunks written; vector dim = 384

==== step 2: Rust ingest (same YAML) ====
embedder loaded (BYO, YAML-driven): byo-demo-bge-small-fp32 (dim=384, repo=Xenova/bge-small-en-v1.5, file=onnx/model.onnx, pooling=Cls)
[rust] 12 chunks written; vector dim = 384

PASS — both languages successfully ingested via YAML-only BYO embedder.
```

The `model_name` is a made-up label (`byo-demo-bge-small-fp32`) that does
**not** match anything in either language's registry. So the only path that
can load this model is the BYO dispatch — that's how we know it's actually
working, not falling through to a registry hit.

## Pooling: CLS vs MEAN

Most retrieval models are one of:

| Family | Pooling | Examples |
|---|---|---|
| BGE family (BAAI) | **CLS** | `BAAI/bge-*-en-v1.5`, `Xenova/bge-*-int8` |
| Sentence-transformers / MiniLM / e5 | **MEAN** | `sentence-transformers/*`, `intfloat/e5-*` |
| Nomic v1.5 | mean (handled internally by fastembed-rs's stock variant) |

If you don't know, check the model's `config.json` for `model_type` or
the tokenizer setup, or default to `cls` and verify the leaderboard
numbers look right after a small bakeoff.

The Rust mean-pooling implementation has unit tests
([`embedder.rs::tests::mean_pool_*`](../../../rust/chunkshop/src/embedder.rs))
that verify it masks padding tokens correctly — this is the bug that bites
naive mean-pooling implementations on short inputs.

## Known gotchas (handled internally; documented so you understand why)

- **Tokenizer-padding normalization (Python).** Some HF-uploaded
  `tokenizer.json` files (notably Xenova sentence-transformers
  conversions) ship with `Fixed=128` padding. fastembed-py's loader
  doesn't override an existing padding config, which produces
  inhomogeneous batches when chunks are longer than 128 tokens (short
  ones pad to 128, long ones stay at natural length → batch tensor fails
  to construct). chunkshop's `FastembedProvider.__init__` re-enables
  padding as `BatchLongest` post-init to handle this universally —
  works for any tokenizer.json regardless of how it was authored.

- **HuggingFace cache reuse:** if the same `hf_repo` was previously
  cached (e.g. you registered the int8 sibling earlier), fastembed-py's
  cache won't auto-fetch new files for a different `model_file`.
  chunkshop's `register_byo_model` works around this by pre-fetching via
  `huggingface_hub.hf_hub_download` before calling `add_custom_model`.

- **`dim` is a contract.** If your YAML says `dim: 768` but the model
  produces 384, both implementations error before writing anything to
  pgvector. No silent corruption.

## What this replaces

Before this brief: "Adding a new model" meant the multi-step Sub-case 3a
walkthrough in [`docs/embedders.md`](../../embedders.md) — edit
`_INT8_VARIANTS`, run `uv sync`, edit `embedder.rs`, run `cargo build`.
Now: edit YAML.

The full Case A/B/C documentation in `docs/embedders.md` is updated to
reflect this. Case B is now "if the registry shortcut is more ergonomic
for you" rather than "the only way."
