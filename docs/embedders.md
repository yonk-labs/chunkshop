# Embedders

chunkshop's Python implementation has one embedder backend today: `fastembed`. Behind it is
ONNX Runtime plus a HuggingFace tokenizer. Every supported model is an ONNX file; int8 is
just a different ONNX file for the same architecture, not a different codepath.

This doc covers: the shipped model catalogue, the int8 registration pattern, how to add a
new model, and how to A/B two embedders on the same corpus.

## Catalogue

| `model_name`                             | dim | Precision | Registered by        | Notes                                  |
|------------------------------------------|-----|-----------|----------------------|----------------------------------------|
| `Xenova/bge-base-en-v1.5-int8`           | 768 | int8      | chunkshop `_registry`| **Default.** Best quality-for-size.    |
| `Xenova/bge-small-en-v1.5-int8`          | 384 | int8      | chunkshop `_registry`| Smaller/faster; ~3–5 fewer MTEB pts.   |
| `BAAI/bge-small-en-v1.5`                 | 384 | fp32      | fastembed (built-in) | fp32 of small; +0–2 pts over int8.     |
| `BAAI/bge-base-en-v1.5`                  | 768 | fp32      | fastembed (built-in) | Quality ceiling for BGE family.        |
| `nomic-ai/nomic-embed-text-v1.5-Q`       | 768 | int8      | fastembed (built-in) | 8k-token context; use for long docs.   |
| `nomic-ai/nomic-embed-text-v1.5`         | 768 | fp32      | fastembed (built-in) | fp32 long-context; ~550 MB.            |

Any model fastembed knows about is usable — `fastembed.TextEmbedding.list_supported_models()`
in a REPL is the full current list. The catalogue above is what the shipped example configs
and factorial YAMLs actually use.

## How it works

```mermaid
flowchart LR
    Y[YAML<br/>embedder.model_name] --> PC[pydantic<br/>FastembedEmbedder]
    PC --> LE[load_embedder]
    LE --> FP[FastembedProvider<br/>fastembed_provider.py]
    FP --> TE[fastembed.TextEmbedding]
    TE --> DL{model cached?}
    DL -- no --> HF[HuggingFace hub<br/>download ONNX + tokenizer]
    HF --> CACHE[~/.cache/fastembed/]
    DL -- yes --> CACHE
    CACHE --> ORT[onnxruntime session]
    FP --> EMB[embed list of text<br/>→ np.float32 array]
```

First invocation of a given `model_name` downloads ONNX + tokenizer to `~/.cache/fastembed/`.
Subsequent invocations are local. Size varies: int8 `bge-base` is ~85 MB, fp32 `nomic` is
~550 MB.

## The int8 registry trick

Fastembed's built-in registry ships the fp32 BGE variants only — its `-onnx-q` entries on
qdrant HF are actually fp32 optimized-ONNX (misleading naming). To use real int8 BGE weights,
chunkshop registers Xenova's community uploads at import time.

The mechanism is in `python/src/chunkshop/embedders/_registry.py`:

```python
_INT8_VARIANTS = [
    {
        "model": "Xenova/bge-small-en-v1.5-int8",
        "dim": 384,
        "pooling": PoolingType.CLS,
        "normalization": True,
        "sources": ModelSource(hf="Xenova/bge-small-en-v1.5"),
        "model_file": "onnx/model_quantized.onnx",
        ...
    },
    # ... plus bge-base-int8
]

def register_int8_variants() -> None:
    for v in _INT8_VARIANTS:
        if v["model"] not in {m["model"] for m in TextEmbedding.list_supported_models()}:
            TextEmbedding.add_custom_model(**v)
```

`embedders/__init__.py` calls `register_int8_variants()` at import, so by the time
`load_embedder` runs, the variants are available to fastembed just like built-in models.

Idempotent — safe to call multiple times. Safe to call even if fastembed starts shipping
these variants natively; the duplicate check skips them.

## Adding a new model

### Case A: fastembed already supports it

Nothing to do in chunkshop. Put it straight in your YAML:

```yaml
embedder:
  type: fastembed
  model_name: sentence-transformers/all-MiniLM-L6-v2
  dim: 384
```

`dim` is a contract — if the model produces a different dimension at runtime,
`FastembedProvider.embed` raises a clear error before writing anything.

### Case B: fastembed doesn't know about it but the HF repo has an ONNX file

Add an entry to `_INT8_VARIANTS` in `python/src/chunkshop/embedders/_registry.py` (the name
is historical — the list holds any registered variant, int8 or not):

```python
{
    "model": "your-org/your-model-name",
    "dim": 768,
    "pooling": PoolingType.CLS,      # or PoolingType.MEAN, check the model card
    "normalization": True,           # usually true for retrieval models
    "sources": ModelSource(hf="your-org/your-model"),
    "model_file": "onnx/model.onnx", # path inside the HF repo
    "description": "short label",
    "license": "...",
    "size_in_gb": 0.123,             # fastembed uses this for download UX
    "additional_files": [
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    ],
},
```

Then reference the new `model` name in your YAML. No code changes elsewhere.

### Case C: you want a different backend (ONNX Runtime directly, not via fastembed)

Add a new provider:

1. Create `python/src/chunkshop/embedders/onnx_direct_provider.py` with an `embed` method.
2. Add an `OnnxDirectEmbedder` pydantic model to `config.py` with `type:
   Literal["onnx_direct"]`, and include it in the `EmbedderConfig` union.
3. Add a branch to `load_embedder` in `embedders/__init__.py`.

The original MVP plan calls for an `onnx_direct` embedder for bit-exact parity checks with
the future Rust/Go ports. Worth doing the day one of those ports ships.

## A/B testing two embedders

The shipped factorial configs are the template. `configs/factorial/` has 12 cells (4
chunkers × 3 embedders × fp32), `configs/factorial-int8/` has the same 12 with int8 swapped
in. Each YAML writes to a different `{schema}.{table}`, so all 24 live side-by-side and a
query script can compare retrieval quality across cells.

### Minimal A/B

Two YAMLs, same everything except the embedder:

```yaml
# cell-a.yaml
cell_name: ab_test_a
source: {type: files, glob: /path/**/*.md, id_from: stem}
chunker: {type: hierarchy}
embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5
  dim: 384
target: {dsn_env: CHUNKSHOP_DSN, schema: ab_test, table: a_bge_small_fp32, overwrite: true}

# cell-b.yaml — same but:
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target: {..., table: b_bge_small_int8, overwrite: true}
```

Run both:

```bash
chunkshop orchestrate -c cell-a.yaml -c cell-b.yaml --concurrency 2
```

Compare by running the same query embedding against both tables and measuring Recall@k or
whatever signal your downstream task cares about.

### The factorial shortcut

```bash
# Point your DSN at a scratch DB.
export AGE_BAKEOFF_PGRG_DSN="postgresql://postgres:postgres@localhost:5434/scratch"

# Smoke test all 24 cells (1 doc each) to confirm nothing's broken:
chunkshop orchestrate --config-dir python/src/chunkshop/configs/factorial --smoke
chunkshop orchestrate --config-dir python/src/chunkshop/configs/factorial-int8 --smoke

# Full run (hours):
chunkshop orchestrate --config-dir python/src/chunkshop/configs/factorial-int8 --concurrency 4
```

Every YAML writes to its own `{schema}.{table}`. No cleanup between cells.

## Benchmark on docs/samples

The quickest way to move past MTEB folklore is to run the A/B on chunkshop's own
sample corpus. The numbers below come from `scripts/bench_embedders.py` running
against `docs/samples/*-*.md` on 2026-04-22.

**Setup.** 4 markdown docs (`handbook-intro`, `handbook-engineering`,
`handbook-security`, `release-notes`), chunked with `hierarchy(prefix_heading=true,
min_section_chars=100)` → 13 chunks per table. Three int8 embedders, same
chunker, same framer, no extractor. 14 hand-written gold queries covering all
four docs, mixing direct-keyword lookups and paraphrased questions (written
before any retrieval ran, so they cannot drift toward a desired answer).

| Embedder                   | recall@1 | recall@3 | recall@5 | MRR   |
|----------------------------|---------:|---------:|---------:|------:|
| `bge-small-int8` (dim 384) |    0.857 |    1.000 |    1.000 | 0.917 |
| `bge-base-int8`  (dim 768) |    0.929 |    1.000 |    1.000 | 0.964 |
| `nomic-q`        (dim 768) |    0.857 |    0.929 |    1.000 | 0.911 |

**Interpretation.** `bge-base-int8` leads by one query at rank 1 and ~0.05 MRR.
On a 14-query corpus that is *one query's difference* — directional signal, not a
statistically significant gap. The honest read: on this tiny corpus all three
embedders are within noise at recall@5, and `bge-base-int8` has a modest edge at
rank 1 that is consistent with what MTEB shows at scale. If you are retrieving
into a small corpus where top-5 is your operating budget, any of the three
will work; if rank 1 matters (e.g., the top chunk goes straight into a prompt),
prefer `bge-base-int8`.

Caveats. 4 docs × 14 queries is low statistical power — a single query flipping
changes aggregate recall by ~0.07. Do not generalize these exact numbers to
your own corpus; run the script against it.

Reproduce:

```bash
# From repo root, with a reachable CHUNKSHOP_TEST_DSN:
uv --project python run python scripts/bench_embedders.py
# Outputs land in skill-output/bench-embedders/{results.json,report.md}
```

Raw results + per-query detail live in `skill-output/bench-embedders/` (gitignored).

## Thread tuning for embedders

`embedder.threads` caps ORT's `intra_op_num_threads` at session creation. Without it,
fastembed auto-detects and sizes the pool to all cores — which is fine when running one
cell, and catastrophic when running four concurrently on a shared box (the pools collide
and you get 4x the contention for no throughput gain).

Rule of thumb:

| Scenario                              | `embedder.threads` | `runtime.omp_num_threads`  |
|---------------------------------------|--------------------|----------------------------|
| Single cell, dedicated machine        | unset (auto)       | match or unset             |
| Single cell, shared dev machine       | 4                  | 4                          |
| `--concurrency 4` on 16 physical cores| 4                  | 4                          |
| `--concurrency 8` on 16 physical cores| 2                  | 2                          |

Goal: `concurrency × threads ≈ physical cores`. Oversubscribing CPU threads always costs
throughput; undersubscribing leaves it on the table.

## Precision guidance

- **int8 is the default for a reason.** ~2× faster ingest, nearly identical retrieval
  quality on the legal QA benchmark (160 vs 152 fully_correct across 12 cells).
- **Disk + memory are cheaper int8.** A 768-dim fp32 vector is 3 KB on the wire; int8
  weights + fp32 vectors still end up roughly half the memory footprint at ingest time.
- **Use fp32 if**: retrieval quality is your top constraint on a high-recall corpus, or
  you're running a bake-off and need the fp32 baseline.
- **Skip `-Q` variants from sources you don't trust**. Quantization is lossy. The benchmark
  data is for the specific variants chunkshop registers; other community `-int8` / `-Q`
  uploads may have worse calibration.

## File map

| File                                              | Role                                   |
|---------------------------------------------------|----------------------------------------|
| `python/src/chunkshop/embedders/base.py`          | `Embedder` Protocol.                   |
| `python/src/chunkshop/embedders/fastembed_provider.py` | Wraps `fastembed.TextEmbedding`.  |
| `python/src/chunkshop/embedders/_registry.py`     | Registers int8 variants at import.     |
| `python/src/chunkshop/embedders/__init__.py`      | `load_embedder` factory.               |
| `python/src/chunkshop/config.py`                  | `FastembedEmbedder` pydantic model.    |
| `python/tests/chunkshop/test_embedder_fastembed.py` | Embed round-trip test.               |
| `python/tests/chunkshop/test_int8_registry.py`    | Registry idempotence + presence test.  |
