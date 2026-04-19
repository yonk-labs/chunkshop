# Python MVP Plan (2026-04-19)

The authoritative task-by-task plan lives in pg-raggraph at
`docs/superpowers/plans/2026-04-19-chunkshop-ingestion-tool.md`.

When that plan was written, chunkshop was going to live inside the pg-raggraph bakeoff tree.
The decision to extract chunkshop into its own standalone monorepo (`yonk-tools/chunkshop/`)
came after Task 1 was already committed.

**Mapping from the pg-raggraph plan to this repo:**

| Pg-raggraph plan path                                           | This repo path                                          |
|-----------------------------------------------------------------|---------------------------------------------------------|
| `benchmarks/age-bakeoff/scripts/chunkshop/`                     | `python/src/chunkshop/`                                 |
| `benchmarks/age-bakeoff/tests/chunkshop/`                       | `python/tests/chunkshop/`                               |
| `benchmarks/age-bakeoff/scripts/chunkshop/configs/`             | `python/src/chunkshop/configs/`                         |
| `benchmarks/age-bakeoff/scripts/factorial-probe-query.py`       | **stays in pg-raggraph** (experiment-specific consumer) |
| `benchmarks/age-bakeoff/results/diagnostics/factorial-probe.*`  | **stays in pg-raggraph**                                |

The pg-raggraph bakeoff consumes chunkshop via a uv path dependency
(`chunkshop = { path = "../../../chunkshop/python", editable = true }`).

**Standalone constraint — consequence for Task 3:** The original plan said Task 3's
`sentence_aware` chunker would wrap `age_bakeoff.chunker.chunk_text`. Standalone means
no pg-raggraph / bakeoff imports. Task 3 ports that chunker's logic (markdown-heading-aware
prose splitter + paragraph/sentence fallback) into `python/src/chunkshop/chunkers/sentence_aware.py`
directly.

**Embedder backend:** Python MVP uses `fastembed` (thin wrapper over onnxruntime). Rust and
Go ports will call `onnxruntime` directly against the same `.onnx` model files fastembed
downloads. A future Python-side `onnx_direct` embedder can be added for bit-exact parity
verification if needed.
