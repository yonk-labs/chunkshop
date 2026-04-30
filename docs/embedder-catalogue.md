# Embedder catalogue — what works, what doesn't, what to pick

A user-facing guide to picking an embedding model for chunkshop. Covers
every model we've tested end-to-end (in both languages where applicable),
known-broken cases, dimensions, max context, quantization tradeoffs, and
how to fit large models into a laptop's RAM via ONNX + int8.

For the *mechanics* of pointing chunkshop at a new model, see
[`docs/embedders.md`](embedders.md). This doc is the **catalogue**.

## TL;DR — start with one of these

| Use case | model_name | dim | size | notes |
|---|---|---|---|---|
| Default, English retrieval | `Xenova/bge-base-en-v1.5-int8` | 768 | ~110 MB | Shipped default. CLS-pooled. |
| Smaller / faster | `Xenova/bge-small-en-v1.5-int8` | 384 | ~35 MB | Same family, ~3-5pp lower MTEB. |
| Long context (8k tokens) | `nomic-ai/nomic-embed-text-v1.5-Q` | 768 | ~140 MB | Mean-pooled, multilingual-leaning. |
| Multilingual | `Xenova/bge-m3` (BYO) | 1024 | ~600 MB | Multilingual + dense. CLS-pooled. |
| Highest quality monolingual | `Xenova/bge-large-en-v1.5` int8 (BYO) | 1024 | ~340 MB | CLS-pooled. Slower ingest. |

The first three are pre-registered: just put the `model_name` in your
YAML. The last two are BYO mode — see `docs/samples/embedder-byo/` for
the four-line YAML pattern.

## What "ONNX + int8" means and why we do it

chunkshop runs every embedder via **ONNX Runtime** — the same C++ engine
PyTorch and TensorFlow models get exported to for production inference.
ONNX gives us:

- **Single file format** for any model — same loader regardless of whether
  the original was in PyTorch, JAX, or TF.
- **CPU inference at speeds competitive with GPU for batch-1 to batch-32
  workloads** that fit in cache.
- **No PyTorch / CUDA / Python ML stack** at runtime. fastembed (Python)
  and `ort` (Rust) wrap ONNX Runtime with thin tokenization layers.

**int8 quantization** is a separate axis. The original model produces
float32 weights (4 bytes per parameter). int8 stores each weight in 1 byte
with a per-tensor scale factor. Tradeoffs:

| Metric | fp32 | int8 |
|---|---|---|
| Disk size | 1× | ~0.25× |
| RAM footprint | 1× | ~0.25× |
| Inference speed | 1× | ~2× faster on CPU (SIMD + cache) |
| Accuracy | reference | ~98-99% of fp32 on retrieval benchmarks |

For chunkshop's retrieval use case, int8 is **near-free quality loss for
2-4× speed and storage wins**. The shipped factorial bakeoff (12 cells on
a 772-doc legal QA corpus) found `int8 ≥ fp32` in aggregate (160 vs 152
fully_correct) — int8 won on speed AND quality in the only specific
comparison we ran. Public MTEB scores show fp32 typically wins by 0-2 pts
on aggregate retrieval. Pick fp32 only if you can show it actually wins on
*your* corpus.

The Xenova int8 uploads are pre-quantized (community uploads of the
`onnx/model_quantized.onnx` ONNX file). You don't need to quantize
anything yourself.

## Verified-working models

Each of these has been ingested end-to-end through both `chunkshop ingest`
(Python) and `chunkshop-rs ingest` (Rust) on a real corpus, with the
output dim confirmed in pgvector.

### Pre-registered (just use the model_name)

| `model_name` | dim | max_tokens | pooling | precision | repo / file |
|---|---:|---:|---|---|---|
| `BAAI/bge-small-en-v1.5` | 384 | 512 | CLS | fp32 | fastembed-rs stock |
| `BAAI/bge-base-en-v1.5` | 768 | 512 | CLS | fp32 | fastembed-rs stock |
| `BAAI/bge-large-en-v1.5` | 1024 | 512 | CLS | fp32 | fastembed-rs stock |
| `Xenova/bge-small-en-v1.5-int8` | 384 | 512 | CLS | int8 | chunkshop registry, Xenova/bge-small/onnx/model_quantized.onnx |
| `Xenova/bge-base-en-v1.5-int8` | 768 | 512 | CLS | int8 | chunkshop registry, Xenova/bge-base/onnx/model_quantized.onnx |
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | 256 | mean | fp32 | fastembed-rs stock |
| `sentence-transformers/all-MiniLM-L6-v2-int8` | 384 | 256 | mean | int8 | chunkshop registry |
| `nomic-ai/nomic-embed-text-v1.5` | 768 | 8192 | mean | fp32 | fastembed-rs stock |
| `nomic-ai/nomic-embed-text-v1.5-Q` | 768 | 8192 | mean | int8 | fastembed-rs stock |

### BYO (point at the HF repo via YAML)

| Description | dim | max_tokens | pooling | precision | hf_repo / onnx_path |
|---|---:|---:|---|---|---|
| Larger BGE — more accuracy ceiling, ~3× slower than `bge-base-int8` | 1024 | 512 | CLS | int8 | `Xenova/bge-large-en-v1.5` / `onnx/model_quantized.onnx` |
| BGE-M3 multilingual + dense | 1024 | 8192 | CLS | int8 | `Xenova/bge-m3` / `onnx/model_quantized.onnx` |
| Jina v2 base, 8k context | 768 | 8192 | mean | int8 | `Xenova/jina-embeddings-v2-base-en` / `onnx/model_quantized.onnx` |

Each verified May 2026 against `docs/samples/handbook-engineering.md` —
ingestion produced expected-dim vectors in both languages.

## Should-work models (same family / same shape, not yet tested)

These follow the same Xenova-int8 conversion pattern as the verified
models. If they work for you, please update this catalogue.

- `Xenova/bge-large-en-v1.5` fp32 (`onnx/model.onnx`) — same repo, fp32 path
- `Xenova/jina-embeddings-v2-small-en` (`onnx/model_quantized.onnx`) — 512 dim, mean
- `Xenova/multilingual-e5-small` / `-base` / `-large` — sentence-transformers conversions
- `Xenova/all-MiniLM-L6-v2` `onnx/model.onnx` (fp32) — 384 dim mean
- `Xenova/gte-small` / `Xenova/gte-base` — Alibaba GTE ONNX conversions

If a `Xenova/<model>` repo on HF has both `tokenizer.json` and
`onnx/model_quantized.onnx` (or `model.onnx`), the YAML pattern in
[`docs/samples/embedder-byo/`](samples/embedder-byo/) should just work.

## Known-broken models (don't try these)

These fail with informative errors. Listed so you can recognize them
quickly.

| Model | Why it fails | Workaround |
|---|---|---|
| `intfloat/e5-small-v2` | The HF repo doesn't have an `onnx/` directory. e5 is published as PyTorch / safetensors. | Use `optimum-cli export onnx --model intfloat/e5-small-v2 ...` and upload the ONNX yourself, OR use a Xenova / sentence-transformers conversion that already has ONNX. |
| `jinaai/jina-embeddings-v3` | Has external-data ONNX (`model.onnx_data` separate file). fastembed-py / fastembed-rs's downloader doesn't fetch the external data. | Use `Xenova/jina-embeddings-v2-base-en` (verified-working) OR fetch the external data file manually into the same snapshot dir. |
| Any LLM-based embedder (Mistral 7B Embed, NV-Embed-v2, etc.) | Multi-GB ONNX file, may not fit in RAM, and inference is too slow for retrieval-time queries. | Stick with sub-billion-param models. The 1024-dim BGE-large family is the practical ceiling. |
| Models that require special tokens prefixed to inputs (e.g. e5 needs `query: ` / `passage: `) | chunkshop doesn't auto-prefix. The model loads but retrieval quality is degraded. | Either pre-prefix in your chunker output, or skip these models. |

## Model size reference (for picking what fits)

ONNX + int8 file sizes (approximate, per the HF Xenova uploads):

| Model | int8 size | fp32 size |
|---|---:|---:|
| MiniLM-L6 | 22 MB | 90 MB |
| BGE-small | 35 MB | 130 MB |
| BGE-base | 110 MB | 440 MB |
| Nomic-v1.5 | 140 MB | 550 MB |
| BGE-large | 340 MB | 1.3 GB |
| BGE-M3 | 600 MB | 2.3 GB |

**ONNX-fit guidance:**

- **Laptop (16 GB RAM, no GPU):** anything int8 up to 600 MB works fine.
  At fp32, BGE-base is your ceiling unless you have RAM to spare.
- **Server (32+ GB RAM):** any of the above. fp32 BGE-large is reasonable.
- **CI / containerized worker (4 GB RAM):** stick with int8 BGE-small or
  MiniLM. The model has to coexist with Python interpreter + ORT C++
  binary + your data.
- **Latency-sensitive queries:** BGE-small int8 embeds a 256-token query
  in ~5ms on a 4-core laptop. BGE-large int8 takes ~20ms.

The bakeoff CLI is your friend here:
[`docs/samples/bakeoff-ntsb/`](samples/bakeoff-ntsb/) shows how to put
multiple models in a matrix and let the gold queries pick the winner on
*your* data, not a public benchmark's data.

## How to add a new model

Three pages of context in [`docs/embedders.md`](embedders.md):

- **Case A:** model already in fastembed and fastembed-rs → just put the
  `model_name` in YAML.
- **Case B (recommended for everything else):** YAML-only "BYO" mode —
  add `hf_repo`, `onnx_path`, `pooling` to your YAML.
- **Case B-legacy:** edit the chunkshop registry source (only if you're
  contributing a permanent registration).

The runnable demo in [`docs/samples/embedder-byo/`](samples/embedder-byo/)
verifies BYO mode end-to-end in both languages.

## Pooling cheat-sheet

| Family | Pooling |
|---|---|
| BGE family (`BAAI/bge-*`, `Xenova/bge-*`) | **CLS** |
| sentence-transformers | **mean** |
| MiniLM / e5 / jina-v2 / nomic | **mean** |
| BGE-M3 | **CLS** |
| Anything from sentence-transformers org by default | **mean** |

When in doubt: try `cls` first, then `mean`, run a small bakeoff against
your gold set, and pick whichever wins. The wrong-pooling case usually
shows as much-worse-than-expected retrieval accuracy, not as a load
failure.

## Why these defaults

chunkshop's shipped `sample.yaml` uses `Xenova/bge-base-en-v1.5-int8`.
Reasoning:

- **bge-base over bge-small** — MTEB shows ~3-5pp gain. On the 772-doc
  legal corpus chunkshop benchmarked on, the gain was ~2pp r@1 on
  retrieval. Gain plateaus above bge-base for English; bge-large is
  ~1pp better for ~3× cost.
- **int8 over fp32** — same factorial showed int8 ≥ fp32 in aggregate.
  Public MTEB shows fp32 ahead by 0-2pp. The 4× speed/storage win is
  almost always worth it.
- **CLS pooling** — required for the BGE family. Don't change without
  changing the model.

If you're unsure, use the default. If you have a real corpus, run the
bakeoff on it.
