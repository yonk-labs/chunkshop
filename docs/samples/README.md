# Samples

Runnable demos for every supported pipeline shape. Each sub-directory is a
self-contained sample with its own README, fixtures, and demo script.

## Worked-example samples (start here)

Each entry has its own README with full walkthrough.

| Sample | What it shows |
|---|---|
| [`bakeoff-ntsb/`](bakeoff-ntsb/README.md) | **Canonical bakeoff** — 4 chunkers × 3 embedders against 20 NTSB aviation-accident reports + 12 gold queries. Same YAML runs from Python AND Rust; cross-language parity verified. **Step 1 of every adoption.** |
| [`sales-crm/`](sales-crm/README.md) | Real OLTP schema, two source paths: ingest from `pg_table` (live database) and from `files` (markdown export). Same vector schema either way. JOIN-via-VIEW pattern for pulling related-table columns into chunk metadata. |
| [`embedder-byo/`](embedder-byo/README.md) | Bring your own HuggingFace ONNX embedder. Four YAML lines — `hf_repo`, `onnx_path`, `pooling`, `dim` — no code edits, no rebuild. Same YAML works in Python and Rust. |
| [`incremental-pg-table/`](incremental-pg-table/README.md) | Watermarked-cursor pattern for incremental ingest from a live Postgres source. Re-runs only pick up rows changed since last successful run. |
| [`inline-mode/`](inline-mode/README.md) | Library / inline mode — embed chunkshop in your service and drive it document-by-document via `Pipeline.ingest_text(...)`. Python AND Rust demos. Includes orphan-cleanup verification on shrinking updates. |
| [`if-oversize/`](if-oversize/README.md) | The `if_oversize` fallback chain — route oversized chunks (e.g. from `neighbor_expand`'s ±N joins) through a secondary chunker before they hit the embedder. Same YAML, Python + Rust. |

## Single-file recipe configs

Tiny YAMLs against the four `handbook-*.md` + `release-notes.md` fixtures in
this directory. Copy-paste-tweak rather than full walkthroughs.

| Config | Recipe |
|---|---|
| [`sample.yaml`](sample.yaml) | **Default.** `hierarchy` chunker + int8 `bge-base`. The shipped recommendation. |
| [`sample-sentence-aware.yaml`](sample-sentence-aware.yaml) | `sentence_aware` chunker + fp32 `bge-small`. Baseline for "what does hierarchy's heading prefix actually buy me". |
| [`sample-neighbor-expand.yaml`](sample-neighbor-expand.yaml) | `neighbor_expand` wrapping `hierarchy` + int8 `bge-base`. Splices ±1 adjacent sections into `embedded_content` for cross-section retrieval. |
| [`sample-semantic.yaml`](sample-semantic.yaml) | `semantic` chunker against `semantic_demo_interview.md` — topic-shift detection with no headings. See [`../tutorial-semantic.md`](../tutorial-semantic.md). |
| [`sample-summary-embed.yaml`](sample-summary-embed.yaml) | `summary_embed` wrapping `hierarchy` — replaces each chunk's `embedded_content` with a lede-extracted summary. Vector targets the gist; `original_content` stays full-fidelity. |
| [`sample-hierarchical.yaml`](sample-hierarchical.yaml) | `hierarchical_summary` wrapping `hierarchy` — emits both fine-grained chunks AND coarse section summaries (sumy / lex_rank), section-aware grouping. Two-tier retrieval. |
| [`sample-multi-source.yaml`](sample-multi-source.yaml) | Schema-flex demo: `mode: create_if_missing` + `source_tag` + `promote_metadata`. Pair with a second cell using `mode: append` to get two sources in one table. Walkthrough in [`../tutorial-multi-source.md`](../tutorial-multi-source.md). |
| [`bakeoff.yaml`](bakeoff.yaml) + [`bakeoff-gold.yaml`](bakeoff-gold.yaml) | Tiny bakeoff against this directory's 4 markdown docs (3 embedders × 2 chunkers = 6 combos, 14 gold queries). For the canonical full bakeoff, use [`bakeoff-ntsb/`](bakeoff-ntsb/README.md). |

## Markdown fixtures

| File | Used by |
|---|---|
| `handbook-intro.md` | Two `##` sections. Default for `sample.yaml` and friends. |
| `handbook-engineering.md` | Four `##` sections, realistic prose. |
| `handbook-security.md` | `##` sections including one deliberately tiny section that gets dropped under `min_section_chars`. |
| `release-notes.md` | Headingless prose — exercises the hierarchy fallback. |
| `framer_demo_handbook.md` | Giant-markdown fixture for [`../tutorial-framers.md`](../tutorial-framers.md) Scenario A. Underscore-named to stay outside `*-*.md` corpus globs. |
| `framer_demo_news.json` | Nested-JSON fixture for [`../tutorial-framers.md`](../tutorial-framers.md) Scenario B. |
| `semantic_demo_interview.md` | Headingless interview transcript with three topic regions. Used by [`../tutorial-semantic.md`](../tutorial-semantic.md) and `sample-semantic.yaml`. |

## Running the recipe configs

```bash
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"

# One cell:
chunkshop ingest --config docs/samples/sample.yaml

# All three baseline recipes side-by-side:
chunkshop orchestrate \
  -c docs/samples/sample.yaml \
  -c docs/samples/sample-sentence-aware.yaml \
  -c docs/samples/sample-neighbor-expand.yaml \
  --concurrency 3
```

Output tables live in schema `chunkshop_samples`. Every row holds both
`original_content` (raw chunk body, for grep/audit) and `embedded_content`
(what hit the embedder), so you can compare them after the fact:

```sql
SELECT doc_id, seq_num,
       metadata->>'heading' AS heading,
       length(original_content) AS orig_len,
       length(embedded_content) AS embed_len
FROM chunkshop_samples.handbook
ORDER BY doc_id, seq_num;
```

All recipe YAMLs set `mode: overwrite` so re-runs are safe; `hnsw: false`
because 4 docs is well under the point where HNSW beats a sequential scan.

## What each recipe shows

### `sample.yaml` — `hierarchy` + int8

Splits on `#`/`##` headings. Each chunk's `embedded_content` is prefixed with the section
heading, so `handbook-security.md`'s "Secrets management" section gets embedded as
`"Secrets management\n\n<body>"`. You'll see one tiny section dropped from
`handbook-security.md` ("See also", ~86 chars < `min_section_chars: 100`).

`release-notes.md` has no real headings, so hierarchy emits one chunk for the whole doc,
prefixed with the document title.

### `sample-sentence-aware.yaml` — paragraph-respecting

Splits on markdown headings first (same as hierarchy), then falls back to paragraph-packing
up to 3000 chars. Unlike hierarchy, this does **not** prepend the heading to the embedded
content. Good baseline for "what does hierarchy's heading prefix actually buy me".

`release-notes.md` (no headings) gets split on blank lines, packed into ≤3000-char chunks.

### `sample-neighbor-expand.yaml` — hierarchy + ±1 context

Runs hierarchy first, then rebuilds each row's `embedded_content` by joining the previous,
current, and next sections. `original_content` stays as the single section (clean for grep),
but the vector sees more context. Useful when retrieval misses answers that span section
boundaries.

### `sample-multi-source.yaml` — schema-flex shape

Same corpus, same chunker, but written via `mode: create_if_missing` +
`source_tag: handbook_markdown` + `promote_metadata: [{path: strategy, type: text}]`.
Demonstrates the multi-source fields on a guaranteed-populated promoted column
(`HierarchyChunker` writes `metadata.strategy = "hierarchy"` on every chunk).
Layer a second cell on top with `mode: append` to see two sources in one table —
full walkthrough in [`../tutorial-multi-source.md`](../tutorial-multi-source.md).

## Comparing results

After running all three baseline recipes, query the same search text against each table and
compare the top-k results:

```sql
-- Replace the vector literal with the embedding of your query string,
-- produced by the same model each cell used (bge-base = 768 dims for hierarchy +
-- neighbor_expand; bge-small = 384 dims for sentence_aware). See
-- ../query-clients.md for copy-paste query samples in Python/JS/Rust/Go.
SELECT
  (SELECT doc_id || '::' || seq_num || ' — ' || (metadata->>'heading') FROM chunkshop_samples.handbook                 ORDER BY embedding <=> '[...]'::vector LIMIT 1) AS hierarchy_top1,
  (SELECT doc_id || '::' || seq_num || ' — ' || (metadata->>'heading') FROM chunkshop_samples.handbook_sentence_aware  ORDER BY embedding <=> '[...]'::vector LIMIT 1) AS sentence_aware_top1,
  (SELECT doc_id || '::' || seq_num || ' — ' || (metadata->>'heading') FROM chunkshop_samples.handbook_neighbor_expand ORDER BY embedding <=> '[...]'::vector LIMIT 1) AS neighbor_expand_top1;
```

For a full structured bake-off harness, see [`bakeoff-ntsb/`](bakeoff-ntsb/README.md).
