# Embedders

chunkshop's embedder stage runs ONNX inference. Same model_name, same ONNX file,
same target table → vectors are interchangeable across implementations:

- **Python:** `fastembed` package → ORT → `~/.cache/fastembed/`.
- **Rust:** `fastembed-rs` for stock variants OR a hand-rolled `ort` path for
  the int8 BGE variants where we want bit-near-exact parity with Python.

Every supported model is an ONNX file; int8 is just a different ONNX file for
the same architecture, not a different codepath.

This doc covers: the shipped model catalogue (with Python/Rust support per
model), the registration patterns in both languages, how to add a new model
in either, and how to A/B two embedders on the same corpus.

## Catalogue

| `model_name`                             | dim | Precision | Python | Rust  | Notes                                  |
|------------------------------------------|-----|-----------|--------|-------|----------------------------------------|
| `Xenova/bge-base-en-v1.5-int8`           | 768 | int8      | ✅      | ✅ bit-near-exact | **Default.** Best quality-for-size.    |
| `Xenova/bge-small-en-v1.5-int8`          | 384 | int8      | ✅      | ✅ bit-near-exact | Smaller/faster; ~3–5 fewer MTEB pts.   |
| `BAAI/bge-small-en-v1.5`                 | 384 | fp32      | ✅      | ✅ stock | fp32 of small; +0–2 pts over int8.     |
| `BAAI/bge-base-en-v1.5`                  | 768 | fp32      | ✅      | ✅ stock | Quality ceiling for BGE family.        |
| `BAAI/bge-large-en-v1.5`                 | 1024| fp32      | ✅      | ✅ stock | Larger; only worth it on long docs.   |
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | fp32      | ✅      | ✅ stock | Fast baseline.                         |
| `sentence-transformers/all-MiniLM-L6-v2-int8` | 384 | int8 | ✅     | ✅ stock | int8 baseline (mean-pooled).           |
| `nomic-ai/nomic-embed-text-v1.5`         | 768 | fp32      | ✅      | ✅ stock | fp32 long-context; ~550 MB.            |
| `nomic-ai/nomic-embed-text-v1.5-Q`       | 768 | int8      | ✅      | ✅ stock | 8k-token context; use for long docs.   |

**"bit-near-exact"** means Rust loads the same ONNX file Python loads,
through a hand-rolled `ort` path with thread-controlled inference. The
two implementations diverge by ULPs (~1-2e-3 mean cosine drift, ~5-15e-3
max — see the parity check). **"stock"** means the model uses
`fastembed-rs`'s built-in pipeline, which handles pooling and tokenization
internally.

Any model fastembed (Python) knows about is usable from the Python side —
`fastembed.TextEmbedding.list_supported_models()` in a REPL is the full
current list. The Rust dispatch is currently a hand-curated subset; see
"Adding a new model in Rust" below.

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

## The Rust dispatch (file map)

Rust has two registration paths because two model families need different
codepaths:

```mermaid
flowchart LR
    Y[YAML<br/>embedder.model_name] --> CFG[FastembedEmbedderConfig]
    CFG --> CHK{user_defined_source?}
    CHK -- yes<br/>(Xenova int8) --> UD[hand-rolled ort path<br/>bit-near-exact CLS-pooled]
    CHK -- no --> RES[resolve_model_name]
    RES --> FB[fastembed-rs<br/>EmbeddingModel variant]
    FB --> ORT[onnxruntime session]
    UD --> ORT
```

Both paths live in `rust/chunkshop/src/embedder.rs`:

- **`user_defined_source(model_name)`** — returns `Some((repo, onnx_path))`
  when the model has a bit-near-exact path. Today: `Xenova/bge-base-en-v1.5-int8`
  and `Xenova/bge-small-en-v1.5-int8`. Both CLS-pooled. Adding a mean-pooled
  model here requires a mean-pooling branch in the hand-rolled forward (it's
  CLS-only today).

- **`resolve_model_name(name)`** — the dispatch table for stock fastembed-rs
  variants. Every name fastembed-rs has built in can be wired with a one-line
  insert into the HashMap. The current shipped list:
  - `BAAI/bge-{small,base,large}-en-v1.5` → `EmbeddingModel::BGE{Small,Base,Large}ENV15`
  - `sentence-transformers/all-MiniLM-L6-v2[-int8]` → `EmbeddingModel::AllMiniLML6V2[Q]`
  - `nomic-ai/nomic-embed-text-v1.5[-Q]` → `EmbeddingModel::NomicEmbedTextV15[Q]`

The error message when a model_name isn't recognized lists the supported
names in both categories — a YAML typo fails at config-load with a clear
list of valid choices.

## Adding a new model

### Case A: both fastembed and fastembed-rs already support it

Nothing to do in chunkshop. Put it straight in your YAML and it works in
both languages:

```yaml
embedder:
  type: fastembed
  model_name: sentence-transformers/all-MiniLM-L6-v2
  dim: 384
```

`dim` is a contract — if the model produces a different dimension at runtime,
both implementations raise a clear error before writing anything.

### Case B: any HuggingFace ONNX file (YAML-only, the recommended path)

If your model isn't built into either fastembed library but **does** have an
ONNX file on HuggingFace, you can point at it from YAML alone. No code
edits, no rebuild.

```yaml
embedder:
  type: fastembed
  model_name: byo-demo-bge-small-fp32   # any unique label
  dim: 384                              # contract — must match runtime output

  hf_repo: Xenova/bge-small-en-v1.5     # where to fetch from
  onnx_path: onnx/model.onnx            # file inside the repo
  pooling: cls                          # "cls" or "mean", default "cls"
  # additional_files: [...]             # optional, defaults sane
```

`hf_repo` and `onnx_path` are paired: set both (BYO mode) or neither
(registry mode). Same YAML works in both languages.

**Pooling choice.** Most retrieval models are one of:

| Family | `pooling:` value |
|---|---|
| BGE family (BAAI / Xenova bge variants) | `cls` |
| sentence-transformers / MiniLM / e5 / nomic | `mean` |

If you're not sure, default to `cls` and run a small bakeoff with both
values to compare. Rust's hand-rolled forward has a mean-pooling branch
that masks padding tokens correctly (verified by
`rust/chunkshop/src/embedder.rs::tests::mean_pool_*`).

**End-to-end demo:**
[`docs/samples/embedder-byo/`](samples/embedder-byo/) — runs both
`chunkshop ingest` and `chunkshop-rs ingest` against a YAML pointing at a
non-registered model. Verifies dim and chunk count from both languages.

**Two fastembed-py quirks chunkshop handles internally:**

- *Tokenizer padding normalization.* Some HF-uploaded `tokenizer.json`
  files (notably Xenova sentence-transformers conversions) ship with
  `Fixed=128` padding. fastembed-py's loader doesn't override existing
  padding, producing inhomogeneous batches for chunks > 128 tokens.
  chunkshop's `FastembedProvider.__init__` post-init normalizes the
  tokenizer to `BatchLongest`, which works for any tokenizer.json
  regardless of how it was authored. Both CLS- and mean-pooled BYO
  models now work end-to-end through Python.
- *Per-repo cache reuse.* fastembed-py's cache reuses a snapshot if it
  exists; a second BYO registration against the same `hf_repo` with a
  different `onnx_path` won't auto-fetch the new file. chunkshop's
  `register_byo_model` pre-fetches via `huggingface_hub.hf_hub_download`
  to side-step this.

### Case B-legacy: register in the hardcoded list (when YAML-pointer doesn't fit)

Pre-Case-B, the way to add a model was to edit the registry in both
languages. That path still works and is the right tool when:

- You want a permanent, "shipped with chunkshop" registration (e.g. you're
  contributing a new default model).
- You need the bit-near-exact Rust path with custom thread-pinning, not
  the YAML-pointer's runtime-loaded variant.

**Python side** — add an entry to `_INT8_VARIANTS` in
`python/src/chunkshop/embedders/_registry.py`:

```python
{
    "model": "your-org/your-model-name",
    "dim": 768,
    "pooling": PoolingType.CLS,      # or PoolingType.MEAN, check the model card
    "normalization": True,
    "sources": ModelSource(hf="your-org/your-model"),
    "model_file": "onnx/model.onnx",
    "description": "short label",
    "license": "...",
    "size_in_gb": 0.123,
    "additional_files": [
        "tokenizer.json", "tokenizer_config.json",
        "special_tokens_map.json", "config.json",
    ],
},
```

**Rust side** — depends on whether `fastembed-rs` already knows about it:

- *If `fastembed-rs::EmbeddingModel::*` has a matching variant:* one-line
  `HashMap` insert in `rust/chunkshop/src/embedder.rs::resolve_model_name`:
  ```rust
  table.insert("your-org/your-model-name", EmbeddingModel::YourModelVariant);
  ```
  Then update the helpful error message to list it. Rebuild. Done.

- *If `fastembed-rs` doesn't have it:* add to `user_defined_source` (CLS-pooled
  models go through the bit-near-exact hand-rolled path). Mean-pooled models
  can use either the registry path with a hardcoded entry OR the Case B
  YAML-pointer (which routes through the same forward pass with `Pooling::Mean`).

### Case C: you want a different backend entirely

Examples: ONNX Runtime directly without fastembed, an HTTP embedder pointing
at a remote API (OpenAI / Cohere / Voyage / TEI).

Add a new provider:

1. **Python:** create `python/src/chunkshop/embedders/<your>_provider.py` with
   an `embed(self, texts: list[str]) -> np.ndarray` method.
2. Add a `<Your>Embedder` pydantic model to `config.py` with `type: Literal["your_type"]`,
   include it in the `EmbedderConfig` discriminated union.
3. Add a branch to `load_embedder` in `embedders/__init__.py`.
4. **Rust:** add a matching variant to `EmbedderConfig` in `config.rs`,
   implement the new path (or wrap your provider in the existing
   `FastembedEmbedder` shape if it conforms).

This is the path for things like external API-backed embedders, where ONNX
isn't involved. Bigger lift than Case B; usually warrants its own brief.

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

## Full-factorial: 5 chunkers x 3 embedders

`scripts/bench_matrix.py` runs every combination of the five canonical chunking
strategies against the three int8 embedders — 15 total cells, same 14 gold
queries, same corpus. Run date 2026-04-22.

Strategies (matching chunkshop's factorial convention):

| Key | Chunker                                               |
|-----|-------------------------------------------------------|
| A   | `sentence_aware`                                      |
| B   | `hierarchy` (default)                                 |
| C   | `fixed_overlap(window_words=300, step_words=150)`     |
| D   | `neighbor_expand(window=1)` over `sentence_aware`     |
| E   | `neighbor_expand(window=1)` over `hierarchy`          |

**MRR grid (higher is better):**

| strategy \ embedder | `bge-small-int8` | `bge-base-int8` | `nomic-q` |
|---------------------|-----------------:|----------------:|----------:|
| A: `sentence_aware`        | 0.917 | 0.929 | 0.871 |
| B: `hierarchy`             | 0.917 | **0.964** | 0.911 |
| C: `fixed_overlap`         | 0.854 | 0.946 | 0.863 |
| D: `neighbor+sentence`     | 0.869 | 0.952 | 0.911 |
| E: `neighbor+hierarchy`    | **0.964** | 0.952 | 0.911 |

Two combos tie for best at MRR=0.964: `hierarchy + bge-base-int8` (the
shipped default) and `neighbor+hierarchy + bge-small-int8`. The latter is
interesting — a smaller embedder closes the gap via context-augmented chunks.
The spread across the whole grid is ~0.11 MRR, which is ~1.5 queries out of
14. Treat anything within ~0.07 of the leader as indistinguishable on this
corpus.

**Chunk counts per combo:**

| strategy \ embedder | `bge-small` | `bge-base` | `nomic-q` |
|---------------------|------------:|-----------:|----------:|
| A                   | 8 | 8 | 8 |
| B                   | 13 | 13 | 13 |
| C                   | 7 | 7 | 7 |
| D                   | 14 | 14 | 14 |
| E                   | 13 | 13 | 13 |

Takeaways:

- `fixed_overlap` is consistently at the bottom — predictable baseline, no
  semantic structure to exploit.
- `hierarchy` leads among non-neighbor strategies on every embedder.
- `neighbor_expand` around `hierarchy` matches or exceeds plain `hierarchy` at
  the cost of more chunks (each chunk includes the ±1 context in its embedded
  text).
- `nomic-q` underperforms both bge variants on this small corpus on every
  strategy — consistent with the single-axis bench above.

Reproduce:

```bash
uv --project python run python scripts/bench_matrix.py
# -> skill-output/bench-matrix/{results.json, report.md}
```

Same caveat as the single-axis bench: 4 docs × 14 queries is low statistical
power. Use this as directional signal and run against your own corpus before
making a load-bearing choice.

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
