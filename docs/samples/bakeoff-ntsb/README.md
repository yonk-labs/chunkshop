# NTSB bakeoff sample — runnable end-to-end (Python AND Rust)

A real bakeoff against the **NTSB aviation-accident corpus** (20 final-report
markdown files shipped with `pg-raggraph/benchmarks/kg-rag-eval/extracted/ntsb/`).
Hand-written 12-query gold set, four chunkers, BGE int8 embedders (Python adds
nomic), one command, one leaderboard.

> **Both languages run this sample.** `chunkshop bakeoff` (Python) reads the
> canonical 12-combo matrix; `chunkshop-rs bakeoff` reads the Rust-compatible
> 8-combo subset (nomic isn't in the Rust embedder registry yet — that's a
> follow-up). The cross-language parity test in
> `scripts/parity_check_bakeoff.py` confirms both rank the 8 overlapping
> combos within 2.5pp MRR and agree on ordering for distinct-MRR pairs.

## What it does

`chunkshop bakeoff` (or `chunkshop-rs bakeoff`) ingests every (chunker × embedder)
combo into its own table under `chunkshop_bakeoff_ntsb`, embeds each gold query
with each embedder, ranks results against the gold doc per query, and writes:

- `skill-output/bakeoff/ntsb_bakeoff/results.json` — raw scored data
- `skill-output/bakeoff/ntsb_bakeoff/report.md` — leaderboard + per-query detail
- `skill-output/bakeoff/ntsb_bakeoff/recommended.yaml` — runnable `ingest` cell for the top combo

## Files

| File | Role |
|---|---|
| [`bakeoff-ntsb.yaml`](bakeoff-ntsb.yaml) | Canonical matrix (Python): 4 chunkers × 3 embedders = 12 combos. Includes nomic. |
| [`bakeoff-ntsb-rust.yaml`](bakeoff-ntsb-rust.yaml) | Rust-compatible matrix: 4 chunkers × 2 BGE embedders = 8 combos. Drops nomic. |
| [`gold-ntsb.yaml`](gold-ntsb.yaml) | 12 hand-written queries, each paired with its gold doc_id (file stem). Shared. |
| [`sample-results-python.md`](sample-results-python.md) | Committed leaderboard from a Python verified run (12 combos). |
| [`sample-results-rust.md`](sample-results-rust.md) | Committed leaderboard from a Rust verified run (8 combos). |
| [`sample-recommended-python.yaml`](sample-recommended-python.yaml) | Top-combo cell from the Python run, ready to `chunkshop ingest`. |
| [`sample-recommended-rust.yaml`](sample-recommended-rust.yaml) | Top-combo cell from the Rust run, ready to `chunkshop-rs ingest`. |

The corpus path is hard-coded in both YAMLs to:
`/home/yonk/yonk-tools/pg-raggraph/benchmarks/kg-rag-eval/extracted/ntsb/*.md`.
Edit if your `pg-raggraph` checkout lives elsewhere.

## Run it (Python)

```bash
export CHUNKSHOP_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd /path/to/chunkshop      # repo root, not python/
cd python && uv sync --extra dev && cd ..

# 12 combos, ~90 seconds on a laptop:
uv run --project python chunkshop bakeoff \
    --config docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml \
    --dsn "$CHUNKSHOP_DSN" \
    --yes
```

## Run it (Rust)

```bash
export CHUNKSHOP_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd /path/to/chunkshop      # repo root
(cd rust && cargo build --release)

# 8 combos, ~120 seconds on a laptop:
./rust/target/release/chunkshop-rs bakeoff \
    --config docs/samples/bakeoff-ntsb/bakeoff-ntsb-rust.yaml \
    --dsn "$CHUNKSHOP_DSN" \
    --yes
```

`--yes` bypasses the matrix-size confirmation prompt. `--keep-schema` preserves
the per-combo tables in Postgres for post-hoc inspection (default behavior
drops the schema after the leaderboard is written).

## Verify cross-language parity

```bash
python3 scripts/parity_check_bakeoff.py
```

This drives both implementations against `bakeoff-ntsb-rust.yaml` (the
8-combo overlap), then asserts:

- per-combo aggregate MRR within ±2.5pp between languages
- ordering agreement on every distinct-MRR pair (gap > 0.005)
- top combo agrees within the tie band

A verified-run snapshot of the diff: 7/8 combos within ±0.011 MRR; 1 outlier
at 0.021 MRR (`hierarchy + bge-small`) — drift-driven, well inside the
documented ORT envelope. All 8 distinct-MRR pairs ranked consistently.
Both languages picked the same top combo (`hierarchy + bge-base-int8`).

## Sample result (verified Python run)

```
Winner: hierarchy + nomic-ai/nomic-embed-text-v1.5-Q (MRR=0.958, r@1=0.917)
```

| # | Chunker | Embedder | r@1 | r@3 | r@5 | MRR |
|---|---|---|---|---|---|---|
| 1 | `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 2 | `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 3 | `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 1.000 | 1.000 | 0.944 |

Full Python leaderboard in [`sample-results-python.md`](sample-results-python.md).

## Sample result (verified Rust run)

```
Winner: hierarchy + Xenova/bge-base-en-v1.5-int8 (MRR=0.933, r@1=0.917)
```

| # | Chunker | Embedder | r@1 | r@3 | r@5 | MRR |
|---|---|---|---|---|---|---|
| 1 | `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 0.917 | 1.000 | 0.933 |
| 2 | `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 0.917 | 1.000 | 0.933 |
| 3 | `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | 0.833 | 1.000 | 1.000 | 0.903 |

Full Rust leaderboard in [`sample-results-rust.md`](sample-results-rust.md).

## Statistical-power note

12 queries is a **low floor**. One query flipping moves aggregate recall@1 by
~0.08, so the gap between rank 1 and rank 5 (~0.10 MRR) is well within the
noise band of this query set. Treat the leaderboard as a smoke test that
proves the matrix runs end-to-end and that **chunker / embedder choice
matters** — not as a tournament result. Add 30+ queries before treating it
as a recommendation.

## What this exercises

- The **`chunkshop bakeoff`** and **`chunkshop-rs bakeoff`** CLIs end-to-end: matrix expansion, per-cell ingest, query embedding, ranking, scoring, report generation, recommended.yaml emission.
- All four **non-semantic chunkers** (`hierarchy`, `sentence_aware`, `fixed_overlap`, `neighbor_expand`) — byte-identical chunks across languages.
- Two BGE int8 embedders at two sizes (384 / 768 dim) — byte-near-exact across languages within the documented ~1e-3 cosine drift envelope.
- **`gold_doc_id`-level scoring** (chunk-level scoring is out of scope for the MVP bakeoff).
- **Cross-language parity** — the same matrix produces equivalent leaderboards in both implementations.

## Going further

- **Add nomic to the Rust embedder registry** — the single gap that prevents Rust from running the canonical 12-combo matrix. Follow-up brief.
- **Try a `semantic` chunker row** by adding `{ type: semantic, breakpoint_percentile: 95 }` under `matrix.chunkers`. Python only today; Rust's semantic chunker has the documented ~1e-3 ORT drift and can flip near-tie chunk boundaries, which is why it's not in the Rust matrix yet.
- **Ship the recommended cell** by pointing `sample-recommended-{python,rust}.yaml` at your production schema and running `chunkshop ingest` (or `chunkshop-rs ingest`) — the YAMLs are runtime-interchangeable at the single-cell layer.
