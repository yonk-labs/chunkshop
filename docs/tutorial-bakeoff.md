# Tutorial: bakeoff — pick the best chunker + embedder for your corpus

Every chunkshop user hits the same wall on day one: "which config?" The
defaults are a reasonable first guess (`hierarchy` chunker + `bge-base-int8`
embedder), but "reasonable first guess" is not the same as "best for your
docs." MTEB leaderboards don't rank on your data. Folklore doesn't either.

`chunkshop bakeoff` settles the question empirically on your actual corpus.
You point it at a YAML that names:

- a corpus (any source chunkshop already knows — files, JSON, pgtable, etc.),
- a set of hand-written gold queries (what a user might actually ask),
- a matrix of chunker x embedder combos to try,
- a target Postgres,

and chunkshop ingests every combo into its own table, embeds every gold query
with every embedder, runs pgvector top-K against every combo's table, scores
recall@k + MRR, and hands back:

- `results.json` — raw per-combo per-query data.
- `report.md` — leaderboard sorted by MRR, plus the honesty note about how
  many queries you'd need for the top combo to be statistically distinguishable
  from #2.
- `recommended.yaml` — a ready-to-run `chunkshop ingest` cell pre-filled with
  the top-MRR combo. Point `source` at your real corpus, run `chunkshop ingest`,
  done.

This walkthrough runs end-to-end against the sample corpus that ships in
`docs/samples/*-*.md`. You can copy the YAML at the end and swap in your own
docs.

## Prereqs

- chunkshop installed. `uv sync --extra dev --extra all-backends` in `python/`.
- A Postgres with pgvector extension reachable. The test DSN the repo uses is
  `postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg`. Export it:
  ```bash
  export CHUNKSHOP_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
  ```
- Run every command from the chunkshop repo root.

## Step 1: author gold queries

A gold query is `{query: "...", gold_doc_id: "..."}`. The `gold_doc_id` is the
`id` that the source assigns — for the `files` source with `id_from: stem`,
it's the filename without extension. For `handbook-security.md` that's
`handbook-security`.

Open `docs/samples/bakeoff-gold.yaml`. You'll see 14 queries like:

```yaml
- {query: "how do we rotate API keys", gold_doc_id: "handbook-security"}
- {query: "who approves a pull request", gold_doc_id: "handbook-engineering"}
- {query: "what changed in the April release", gold_doc_id: "release-notes"}
```

Write queries a real user would type. Don't paraphrase document titles — that's
a trivial retrieval task. Think about the problem the user is actually trying
to solve when they'd reach for your docs.

**On statistical power.** 14 queries is the floor. One query flipping moves
aggregate recall by `1/14 ≈ 0.07`. Combos within ~0.14 of the leader are
indistinguishable — you're measuring noise. If you care about a reliable
winner, write 30+ queries. The `report.md` prints this caveat at the bottom
every time, scaled to whatever `n_queries` you actually supplied.

## Step 2: author the bakeoff config

Open `docs/samples/bakeoff.yaml`. The shape:

```yaml
name: samples_bakeoff       # becomes the output-dir name + log prefix

source:                     # any chunkshop source — files, json_corpus, pg_table, ...
  type: files
  glob: docs/samples/*-*.md
  id_from: stem

framer:                     # optional; identity is the default
  type: identity

gold_queries: docs/samples/bakeoff-gold.yaml   # path OR inline list

matrix:
  embedders:
    - {type: fastembed, model_name: Xenova/bge-small-en-v1.5-int8, dim: 384}
    - {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    - {type: fastembed, model_name: nomic-ai/nomic-embed-text-v1.5-Q, dim: 768}
  chunkers:
    - {type: hierarchy}
    - {type: sentence_aware}

target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_bakeoff_samples

scoring:
  k: [1, 3, 5]
  include_mrr: true
  top_k: 5
```

The `matrix` block is the cross-product: 3 embedders x 2 chunkers = 6 combos.
Each combo ingests all 4 docs into its own table under
`chunkshop_bakeoff_samples.*`. If you try a matrix bigger than 50 cells, the
CLI prompts for confirmation — pass `--yes` to skip.

## Step 3: run the bakeoff

```bash
chunkshop bakeoff --config docs/samples/bakeoff.yaml
```

Expected: the CLI prints one line per combo as it ingests, then prints the
winner + output paths:

```
Running bakeoff 'samples_bakeoff' — 6 combos
...
Winner: hierarchy + Xenova/bge-base-en-v1.5-int8 (MRR=0.893, r@1=0.857)
Results: skill-output/bakeoff/samples_bakeoff/results.json
Report:  skill-output/bakeoff/samples_bakeoff/report.md
Recommended cell: skill-output/bakeoff/samples_bakeoff/recommended.yaml
```

On a modern laptop with the int8 models already cached this runs in 20-30s
for the 6-combo matrix. First run adds model download time (~200 MB across
the three models; one-time cost under `~/.cache/fastembed/`).

By default the bakeoff schema is dropped on exit — `--keep-schema` keeps it
so you can poke at the tables. Use `--keep-schema` when debugging a
surprising leaderboard.

## Step 4: read the report

Open `skill-output/bakeoff/samples_bakeoff/report.md`. You'll see:

1. **Run metadata** — date, corpus, query count, combo count.
2. **Leaderboard** — every combo sorted by MRR descending, with recall@k
   columns for each k you configured in `scoring.k`.
3. **Per-query detail** — for each combo, one row per query showing the top-1
   hit. This is where you diagnose "why did this combo lose?" — if a combo's
   top-1 for a query is the wrong doc but the right doc is in top-3, that
   tells you something different than a clean miss.
4. **Statistical power note** — the honesty reminder about how big a delta
   you'd need to call the winner real.

Things to look for:

- **A clear leader (MRR > 0.8 and a > 0.1 gap over #2).** You can trust the
  recommendation.
- **Two or three combos clustered within 0.05.** Noise. Pick the cheaper one
  (smaller embedding dim = faster queries, lower storage).
- **Everything under 0.5.** Your gold queries are too hard for this corpus,
  or your corpus is too small, or your gold_doc_ids don't match the source's
  actual IDs. Re-check the doc IDs first.

## Step 5: use the recommended.yaml

`skill-output/bakeoff/samples_bakeoff/recommended.yaml` is a full
`chunkshop ingest` cell config pre-filled with the winning chunker + embedder:

```yaml
'# NOTE': Top combo from bakeoff 'samples_bakeoff' (MRR=0.893, r@1=0.857). Point
  `source` at your real corpus before running `chunkshop ingest`.
cell_name: samples_bakeoff_recommended
source:
  type: files
  glob: docs/samples/*-*.md
  id_from: stem
framer:
  type: identity
chunker:
  type: hierarchy
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_bakeoff_samples
  table: samples_bakeoff_production
  mode: overwrite
```

Edit `source` to point at your real corpus. Edit `target.table` to the name
you want in production. Then:

```bash
chunkshop ingest --config skill-output/bakeoff/samples_bakeoff/recommended.yaml
```

That's the full loop: measure on your data → get a runnable cell → ship it.

## When to re-run a bakeoff

- Your corpus changes materially (2x growth, a new doc type, schema drift).
- A new embedder ships and you want to see if the jump is worth the migration.
- You change the gold queries (added more, refined existing, covered a new
  topic). The ranking may shift.

The bakeoff is cheap relative to wrong chunker+embedder choices bleeding
retrieval quality into every downstream query. Budget one every couple of
months against a fresh sample of your traffic.
