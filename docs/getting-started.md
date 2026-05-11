# Getting started — the chunkshop user journey, end-to-end

This doc walks the canonical chunkshop loop using a real corpus: 20 NTSB
aviation-accident reports shipped under `pg-raggraph/benchmarks/kg-rag-eval/`.
By the end, you'll have:

- A leaderboard ranking 12 (chunker × embedder) combos against 12 hand-written gold queries
- A `recommended.yaml` cell containing the winning combo
- That cell ingested into your pgvector database, ready to query

Time budget: **~10 minutes** of reading, **~90 seconds** of bakeoff compute on a laptop.

## The journey, in one diagram

```
┌─────────────┐     ┌────────────┐     ┌──────────────────┐     ┌─────────────┐     ┌────────────┐
│ 1. corpus   │ ──▶ │ 2. gold    │ ──▶ │ 3. bakeoff       │ ──▶ │ 4. ship the │ ──▶ │ 5. new     │
│   (real     │     │   queries  │     │   chunker ×      │     │   recommended│    │   corpus → │
│    data)    │     │   (~10)    │     │   embedder       │     │   cell to   │     │   repeat   │
│             │     │            │     │   matrix         │     │   production │    │   from 2   │
└─────────────┘     └────────────┘     └──────────────────┘     └─────────────┘     └────────────┘
                                              │
                                              ▼
                                       ┌──────────────┐
                                       │ leaderboard  │
                                       │ + recommended│
                                       │   .yaml      │
                                       └──────────────┘
```

## Prerequisites

- A reachable Postgres with the `pgvector` extension
- Python 3.12+ and `uv` (or `pip`)
- Optional: `chunkshop-rs` if you want to run **step 4** (ingest) in Rust. Steps 1–3 (the bakeoff) are Python today; the Rust port is in flight.

```bash
# Quickest path to a pgvector-enabled Postgres:
docker run -d --name chunkshop-pg -p 5432:5432 \
    -e POSTGRES_PASSWORD=postgres \
    pgvector/pgvector:pg16

export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/postgres"

# Install chunkshop
git clone https://github.com/yonk-labs/chunkshop && cd chunkshop
cd python && uv sync --extra dev --extra all-backends && cd ..
```

## Step 1: Bring real data

For this walkthrough we use a checked-in corpus from a sibling repo:

```bash
ls docs/samples/bakeoff-ntsb/corpus/*.md | wc -l
# 20
```

Each file is one NTSB final-accident report. Reasonably structured markdown with a heading and ~5 pages of analysis. Realistic-but-bounded — small enough that the bakeoff finishes in ~90 seconds.

If you're following along with your *own* corpus, point `source.glob` in the bakeoff YAML at it. Anywhere you have markdown files (or a Postgres table, or an S3 bucket — see [`docs/samples/`](samples/) for variants) chunkshop can read.

## Step 2: Write a small gold set

The gold set is a list of `{query, gold_doc_id}` pairs. ~10 queries is the floor — fewer and the leaderboard is too noisy to interpret. The NTSB sample ships 12.

[`docs/samples/bakeoff-ntsb/gold-ntsb.yaml`](samples/bakeoff-ntsb/gold-ntsb.yaml) is the canonical example:

```yaml
- { query: "elderly pilot accident on private grass airstrip with trees and utility wires", gold_doc_id: "20071229X02007" }
- { query: "Beech A23 hard landing porpoise nose gear collapse Death Valley",                gold_doc_id: "20071231X02009" }
- { query: "Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin",             gold_doc_id: "20080104X00020" }
# ... 9 more
```

The trick: write queries the way a *user* would, not the way a *retrieval engineer* would. Real users don't say "documents matching predicate X." They say "the one about the elderly pilot in Pennsylvania." Match that energy.

The `gold_doc_id` is just the file stem (chunkshop's `id_from: stem` default). Multiple queries can point at the same doc.

## Step 3: Run the bakeoff

The matrix lives in [`docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml`](samples/bakeoff-ntsb/bakeoff-ntsb.yaml):

```yaml
matrix:
  embedders:
    - { type: fastembed, model_name: "Xenova/bge-small-en-v1.5-int8", dim: 384 }
    - { type: fastembed, model_name: "Xenova/bge-base-en-v1.5-int8",  dim: 768 }
    - { type: fastembed, model_name: "nomic-ai/nomic-embed-text-v1.5-Q", dim: 768 }
  chunkers:
    - { type: hierarchy }
    - { type: sentence_aware }
    - { type: fixed_overlap }
    - { type: neighbor_expand, base: { type: hierarchy }, window: 1 }
```

3 embedders × 4 chunkers = 12 combos. Run it:

```bash
chunkshop bakeoff --config docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml \
                  --dsn "$CHUNKSHOP_DSN" --yes
```

`--yes` skips the matrix-size confirmation prompt. The first run downloads ~3 ONNX model files (~150 MB total, cached for next time). After that, expect ~90 seconds wall on a 4-core laptop.

When it's done, you get three files:

| File | What |
|---|---|
| `skill-output/bakeoff/ntsb_bakeoff/results.json` | Raw scored data — every (combo, query) pair with its top-5 ranking |
| `skill-output/bakeoff/ntsb_bakeoff/report.md`    | Leaderboard + per-query detail, sorted by MRR |
| `skill-output/bakeoff/ntsb_bakeoff/recommended.yaml` | The winning combo as a runnable `chunkshop ingest` cell |

The leaderboard for our verified run ([`docs/samples/bakeoff-ntsb/sample-results.md`](samples/bakeoff-ntsb/sample-results.md)) looks like:

| # | Chunker | Embedder | r@1 | r@3 | r@5 | MRR |
|---|---|---|---|---|---|---|
| 1 | `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 2 | `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 3 | `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 1.000 | 1.000 | 0.944 |
| ... | ... | ... | ... | ... | ... | ... |
| 12 | `fixed_overlap(window=300, step=150)` | `Xenova/bge-base-en-v1.5-int8` | 0.667 | 0.667 | 0.833 | 0.700 |

11 of 12 queries get a top-1 hit on the winner. **`hierarchy` + `nomic-embed-v1.5-Q`** wins MRR=0.958.

### Statistical-power note

12 queries is a **low floor**. One query flipping moves aggregate recall@1 by ~0.08. The gap between rank 1 and rank 5 in the table above (~0.10 MRR) is well within the noise band of this query set. Treat the leaderboard as a **smoke test that proves chunker/embedder choice matters** — not as a tournament result. Add 30+ queries before treating it as a production recommendation.

## Step 4: Ship the recommended cell

The bakeoff already wrote a runnable cell at `skill-output/bakeoff/ntsb_bakeoff/recommended.yaml`:

```yaml
# NOTE: Top combo from bakeoff 'ntsb_bakeoff' (MRR=0.958, r@1=0.917). Point `source`
#       at your real corpus before running `chunkshop ingest`.
cell_name: ntsb_bakeoff_recommended
source:
  type: files
  glob: docs/samples/bakeoff-ntsb/corpus/*.md
  id_from: stem
chunker:
  type: hierarchy
  prefix_heading: true
  min_section_chars: 100
  max_chars: 2000
embedder:
  type: fastembed
  model_name: nomic-ai/nomic-embed-text-v1.5-Q
  dim: 768
target:
  schema: chunkshop_bakeoff_ntsb
  table: ntsb_bakeoff_production
  mode: overwrite
```

Edit the `source` block to point at *your* corpus (or your production Postgres table, or your S3 bucket), pick the target schema/table you want, then run:

**Python:**
```bash
chunkshop ingest --config skill-output/bakeoff/ntsb_bakeoff/recommended.yaml
```

**Rust** *(once `chunkshop-rs ingest` is on your PATH)*:
```bash
chunkshop-rs ingest --config skill-output/bakeoff/ntsb_bakeoff/recommended.yaml
```

Same YAML. Same target table. Same chunk ordering. The cell-level pipeline is at parity, so vectors written by either implementation are interchangeable. Pick whichever runtime fits your deployment.

> Reminder: today only Python can do step 3 (the bakeoff). The Rust port is in flight. Once it lands, `chunkshop-rs bakeoff` will accept the same YAML and produce a leaderboard whose ordering matches Python's (modulo a documented tie band for combos within ~5e-3 MRR of each other).

## Step 5: New corpus → repeat from step 2

The whole point: every new corpus gets its own bakeoff. The recipe that won on NTSB reports may not win on, say, sales notes (different language register, different doc length, different gold-query shape). Don't reuse a recommendation across domains without re-baking.

The pattern:

1. Point a new bakeoff YAML at the new corpus
2. Write 10–30 gold queries against the new corpus
3. Run `chunkshop bakeoff`
4. Take the new `recommended.yaml` to production for that domain

Multiple corpora share a database via the `target.schema` and `target.source_tag` fields — different cells write into different schemas (or the same schema with different `source_tag` for multi-source filtering). See [`docs/incremental.md`](incremental.md) for the multi-source / incremental-ingest patterns.

## What to read next

- [`docs/samples/bakeoff-ntsb/`](samples/bakeoff-ntsb/) — the NTSB sample (config + gold + verified results)
- [`docs/chunkers.md`](chunkers.md) — the chunker catalogue and why each one matters
- [`docs/embedders.md`](embedders.md) — the embedder catalogue
- [`docs/incremental.md`](incremental.md) — patterns for the "step 5" loop (cron, watermarked cursors, CDC, library/inline mode)
- [`docs/quickstart-bakeoff.md`](quickstart-bakeoff.md) — recipe card for bakeoff variants (embedder-only, chunker-only, full factorial)
- [`docs/architecture.md`](architecture.md) — module-by-module breakdown for contributors
