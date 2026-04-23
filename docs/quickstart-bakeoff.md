# Quickstart: bakeoff recipes

Copy-paste YAML for the three bakeoff shapes people actually run. Walk
through [`tutorial-bakeoff.md`](tutorial-bakeoff.md) first if you haven't — it
explains gold queries, the honesty note, and how to read the leaderboard.

## Decision tree

**"I want to compare just embedders (chunker is locked in)."**
→ Fix one chunker in `matrix.chunkers`, list N embedders. N combos total.

**"I want to find the best chunker (embedder is locked in)."**
→ List N chunkers in `matrix.chunkers`, fix one embedder. N combos total.

**"I want the full factorial."**
→ Whatever M chunkers and N embedders you care about. M*N combos. If
M*N > 50, pass `--yes` to skip the confirmation prompt.

## Recipe A: embedder-only bakeoff

You've already settled on `hierarchy` (or you trust chunkshop's
bench-backed default there). You want to know whether `bge-base-int8`, a
larger fp32 model, or a long-context model wins on your docs.

```yaml
name: pick_my_embedder

source:
  type: files
  glob: path/to/your/corpus/*.md
  id_from: stem

gold_queries: path/to/your/gold.yaml

matrix:
  embedders:
    - {type: fastembed, model_name: Xenova/bge-small-en-v1.5-int8, dim: 384}
    - {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    - {type: fastembed, model_name: nomic-ai/nomic-embed-text-v1.5-Q, dim: 768}
    # Add a full-precision option if storage/latency is cheap:
    # - {type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768}
  chunkers:
    - {type: hierarchy}

target:
  dsn_env: CHUNKSHOP_DSN
  schema: bakeoff_embedder_pick

scoring: {k: [1, 3, 5], include_mrr: true, top_k: 5}
```

## Recipe B: chunker-only bakeoff

You're committed to an embedder (cost, latency, or deployment pinning the
choice). You want the best chunker *for that embedder*.

```yaml
name: pick_my_chunker

source:
  type: files
  glob: path/to/your/corpus/*.md
  id_from: stem

gold_queries: path/to/your/gold.yaml

matrix:
  embedders:
    - {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
  chunkers:
    - {type: hierarchy}
    - {type: sentence_aware}
    - {type: fixed_overlap, window_words: 300, step_words: 150}
    - {type: neighbor_expand, window: 1, base: {type: hierarchy}}

target:
  dsn_env: CHUNKSHOP_DSN
  schema: bakeoff_chunker_pick

scoring: {k: [1, 3, 5], include_mrr: true, top_k: 5}
```

## Recipe C: full factorial (chunker x embedder)

You want the clean head-to-head. M chunkers x N embedders combos.

```yaml
name: full_factorial

source:
  type: files
  glob: path/to/your/corpus/*.md
  id_from: stem

gold_queries: path/to/your/gold.yaml

matrix:
  embedders:
    - {type: fastembed, model_name: Xenova/bge-small-en-v1.5-int8, dim: 384}
    - {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    - {type: fastembed, model_name: nomic-ai/nomic-embed-text-v1.5-Q, dim: 768}
  chunkers:
    - {type: hierarchy}
    - {type: sentence_aware}
    - {type: fixed_overlap, window_words: 300, step_words: 150}
    - {type: neighbor_expand, window: 1, base: {type: hierarchy}}

target:
  dsn_env: CHUNKSHOP_DSN
  schema: bakeoff_full

scoring: {k: [1, 3, 5], include_mrr: true, top_k: 5}
```

That's 12 combos — well under the 50-cell confirmation threshold. If you
grow past 50 (e.g., 10 chunkers x 6 embedders), pass `--yes`:

```bash
chunkshop bakeoff --config bakeoff.yaml --yes
```

## Running and reading output

```bash
export CHUNKSHOP_DSN=postgresql://user:pass@host:5432/db
chunkshop bakeoff --config your-bakeoff.yaml
```

Outputs land in `skill-output/bakeoff/{name}/`:

- `results.json` — raw per-combo per-query data, round-trips through pydantic.
- `report.md` — leaderboard + per-query detail + statistical-power note.
- `recommended.yaml` — the top-MRR combo as a runnable `chunkshop ingest`
  cell. Edit `source.glob` / `target.table` and run it.

**Keep the schema for debugging:** pass `--keep-schema` to skip the
`DROP SCHEMA CASCADE` on exit. Useful when a surprising leaderboard needs a
hand-eyeball of a combo's actual top-K results in psql.

## Gold-query tips

- Doc-id-level gold only (MVP). `{query, gold_doc_id}`.
- Write what a real user would type. "What is the scope of our security
  audit cadence?" is a query. "security handbook" is a search term — don't
  confuse them.
- 14 queries is a floor. 30+ gives you enough statistical power for small
  deltas. The honesty note at the bottom of `report.md` scales to your
  actual query count so you never have to eyeball it.
