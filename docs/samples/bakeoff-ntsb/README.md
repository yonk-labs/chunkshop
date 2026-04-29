# NTSB bakeoff sample — runnable end-to-end

A real bakeoff against the **NTSB aviation-accident corpus** (20 final-report
markdown files shipped with `pg-raggraph/benchmarks/kg-rag-eval/extracted/ntsb/`).
Hand-written 12-query gold set, four chunkers, three embedders, one command,
one leaderboard.

> **Python-only today.** The `chunkshop bakeoff` CLI is implemented in
> `python/src/chunkshop/bakeoff/`. The Rust port (`chunkshop-rs bakeoff`)
> is in flight — once it lands this README will document both invocations
> against the same `bakeoff-ntsb.yaml` and ship `sample-results-rust.md`
> alongside the existing `sample-results.md`. Until then, run the bakeoff
> from Python and use the recommended.yaml output to drive `chunkshop-rs
> ingest` if you want the production runtime in Rust.

## What it does

`chunkshop bakeoff` ingests every (chunker × embedder) combo into its own
table under `chunkshop_bakeoff_ntsb`, embeds each gold query with each
embedder, ranks results against the gold doc per query, and writes:

- `skill-output/bakeoff/ntsb_bakeoff/results.json` — raw scored data
- `skill-output/bakeoff/ntsb_bakeoff/report.md` — leaderboard + per-query detail
- `skill-output/bakeoff/ntsb_bakeoff/recommended.yaml` — runnable `chunkshop ingest` cell for the top combo

## Files

| File | Role |
|---|---|
| [`bakeoff-ntsb.yaml`](bakeoff-ntsb.yaml) | The matrix: 4 chunkers × 3 embedders = 12 combos |
| [`gold-ntsb.yaml`](gold-ntsb.yaml) | 12 hand-written queries, each paired with its gold doc_id (file stem) |
| [`sample-results.md`](sample-results.md) | Committed leaderboard from a verified run (full report.md from the bakeoff CLI) |
| [`sample-recommended.yaml`](sample-recommended.yaml) | The cell for the top combo, ready to `chunkshop ingest` against your real corpus |

The corpus path is hard-coded to:
`/home/yonk/yonk-tools/pg-raggraph/benchmarks/kg-rag-eval/extracted/ntsb/*.md`.
Edit `bakeoff-ntsb.yaml` if your `pg-raggraph` checkout lives elsewhere.

## Run it

```bash
export CHUNKSHOP_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
cd /path/to/chunkshop      # repo root, not python/

# Default install (no extras needed for the chunkers/embedders this matrix uses):
uv sync --project python --extra dev

# 12 combos, ~90 seconds total on a laptop:
uv run --project python chunkshop bakeoff \
    --config docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml \
    --dsn "$CHUNKSHOP_DSN" \
    --yes
```

`--yes` bypasses the matrix-size confirmation prompt. `--keep-schema` preserves
the per-combo tables in Postgres for post-hoc inspection (default behavior
drops the schema after the leaderboard is written).

## Sample result (verified run, committed)

The full leaderboard + per-query detail lives in
[`sample-results.md`](sample-results.md). The cell for the winning combo is
in [`sample-recommended.yaml`](sample-recommended.yaml).

```
Winner: hierarchy + nomic-ai/nomic-embed-text-v1.5-Q (MRR=0.958, r@1=0.917)
```

Top 3 combos:

| # | Chunker | Embedder | r@1 | r@3 | r@5 | MRR |
|---|---|---|---|---|---|---|
| 1 | `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 2 | `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 3 | `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 1.000 | 1.000 | 0.944 |

11 of 12 queries get a top-1 hit on the winner. The miss is the cross-doc
ambiguous query *"pilot completed three takeoffs and full stop landings before
runway switch"* — multiple NTSB reports describe similar maneuvers.

## Statistical-power note

12 queries is a low floor. One query flipping moves aggregate recall@1 by
~0.08, so the gap between rank 1 and rank 5 (~0.10 MRR) is well within the
noise band of this query set. Treat the leaderboard as a smoke test that
proves the matrix runs end-to-end and that **chunker / embedder choice
matters** — not as a tournament result. Add 30+ queries before treating it
as a recommendation.

## What this exercises

- The **`chunkshop bakeoff` CLI** end-to-end: matrix expansion, per-cell ingest, query embedding, ranking, scoring, report generation.
- All four **non-semantic chunkers** (`hierarchy`, `sentence_aware`, `fixed_overlap`, `neighbor_expand`).
- Three **embedders** at two sizes: BGE-small int8 (384), BGE-base int8 (768), Nomic v1.5-Q (768).
- **`gold_doc_id`-level scoring** (chunk-level scoring is out of scope for the MVP bakeoff).

## Going further

- **Add the SCOTUS corpus** when the JSON corpus ships in `pg-raggraph`. The bakeoff loader supports `type: json_corpus` directly — swap the source block; everything else stays the same.
- **Try a `semantic` chunker row** by adding `{ type: semantic, breakpoint_percentile: 95 }` under `matrix.chunkers`. Adds boundary-model load time but tests the topic-shift split path.
- **Ship the recommended cell** by pointing `recommended.yaml` at your production schema and running `chunkshop ingest --config skill-output/bakeoff/ntsb_bakeoff/recommended.yaml`.
