# NTSB bakeoff sample — runnable end-to-end (Python AND Rust)

A real bakeoff against the **NTSB aviation-accident corpus** (20 final-report
markdown files shipped with `pg-raggraph/benchmarks/kg-rag-eval/extracted/ntsb/`).
Hand-written 12-query gold set, four chunkers, three embedders, one command,
one leaderboard.

> **One canonical YAML runs from both languages.** `chunkshop bakeoff` and
> `chunkshop-rs bakeoff` both consume `bakeoff-ntsb.yaml`. The
> cross-language parity test in `scripts/parity_check_bakeoff.py` confirms
> the 12 combos rank within ±2.5pp MRR with consistent ordering on
> distinct-MRR pairs.

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
| [`bakeoff-ntsb.yaml`](bakeoff-ntsb.yaml) | The matrix: 4 chunkers × 3 embedders = 12 combos. Runs from both Python and Rust. |
| [`gold-ntsb.yaml`](gold-ntsb.yaml) | 12 hand-written queries, each paired with its gold doc_id (file stem). |
| [`sample-results-python.md`](sample-results-python.md) | Committed leaderboard from a Python verified run. |
| [`sample-results-rust.md`](sample-results-rust.md) | Committed leaderboard from a Rust verified run. |
| [`sample-recommended-python.yaml`](sample-recommended-python.yaml) | Top-combo cell from the Python run, ready to `chunkshop ingest`. |
| [`sample-recommended-rust.yaml`](sample-recommended-rust.yaml) | Top-combo cell from the Rust run, ready to `chunkshop-rs ingest`. |

The corpus path is hard-coded in the YAML to:
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

# 12 combos, ~3 minutes on a laptop:
./rust/target/release/chunkshop-rs bakeoff \
    --config docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml \
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

This drives both implementations against `bakeoff-ntsb.yaml` (the canonical
12-combo matrix), then asserts:

- per-combo aggregate MRR within ±2.5pp between languages
- ordering agreement on every distinct-MRR pair (gap > 0.005)
- top combo agrees within drift tolerance

A verified-run snapshot: 12/12 combos within ±0.021 MRR (max delta
0.021, mean ~0.008); all distinct-MRR pairs ranked consistently. Top combos
ranked tied at 0.958 in Python (sort picked `hierarchy + nomic`); in Rust
the tie broke the other way (`sentence_aware + nomic`) — the two combos are
within drift tolerance, both legitimate winners.

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
Winner: sentence_aware + nomic-ai/nomic-embed-text-v1.5-Q (MRR=0.958, r@1=0.917)
```

| # | Chunker | Embedder | r@1 | r@3 | r@5 | MRR |
|---|---|---|---|---|---|---|
| 1 | `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 2 | `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 0.917 | 1.000 | 0.938 |
| 3 | `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 0.917 | 0.917 | 0.933 |

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
