# `chunkshop fact-search`

**Module**: `chunkshop.cli:fact_search`
**Type**: CLI subcommand
**Ship status**: verified
**Optional extra**: `chunkshop[lede]` (only for `--summary`)
**Since**: 0.7.0

## Purpose

Search a cell's *facts* — the `kind='fact'` rows emitted by the
`consolidation` chunker's `lede` / `lede_spacy` modes (see
[`consolidator-fact-extractors`](consolidator-fact-extractors.md)) — and
return each fact together with a breadcrumb back to the chunk and
document it came from.

This is the read side of fact extraction. Where `chunkshop search`
*excludes* facts by default so they don't pollute chunk results (see
[`cli-search`](cli-search.md)), `fact-search` queries facts directly:
it restricts the hybrid search to `kind='fact'` rows, then resolves
each hit's originating chunk via `metadata.source_chunk_seq` so you get
the `{subject, predicate, object, support_span, confidence}` triple
plus the chunk it was distilled from.

## Usage

```
chunkshop fact-search --config CFG --query QUERY [OPTIONS]
```

## Options

| Option                | Type   | Default      | Notes |
|-----------------------|--------|--------------|-------|
| `--config`            | path   | **Required** | Cell YAML/JSON config. |
| `--query`             | string | **Required** | Free-text query string. |
| `--k`                 | int    | `10`         | Max facts to return. |
| `--confidence-floor`  | float  | `0.0`        | Drop facts whose confidence is below this. Facts with no confidence (`null`) are **always kept**. |
| `--summary/--no-summary` | flag | `--no-summary` | Attach a lede summary of each fact's source chunk. Requires the `[lede]` extra. |
| `--json`              | flag   | off          | Emit JSON instead of human-readable text. |

## Behavior contract

1. **Loads cell config + embedder.** Calls
   `chunkshop.embedders.load_embedder(cfg.embedder)` and embeds the
   query once, exactly like `chunkshop search`.
2. **Restricts to facts.** Calls
   `chunkshop.search_common.search(...)` with
   `where={"metadata": {"kind": "fact"}}` and `return_mode="chunks"`,
   so only `kind='fact'` rows are eligible hits. The fact's
   `original_content` / hit text *is* its `support_span`.
3. **Resolves the breadcrumb.** For each fact hit it reads
   `metadata.source_chunk_seq` and, when present, fetches the parent
   chunk by `(doc_id, seq_num)` via a short-lived per-call connection
   (`_fetch_chunk`). At large `--k` this is N+1 connections — a
   deliberate, convention-matching choice for the small-k interactive
   lookup case. A fact whose `source_chunk_seq` is missing or whose
   parent row is gone yields `chunk: null` (`(no source chunk)` in text
   output).
4. **`--confidence-floor` semantics.** Facts whose `confidence` is a
   number below the floor are dropped. **Facts with `confidence` of
   `null` are always kept** — the filter only fires when a numeric
   confidence is present (`conf is not None and conf < floor`). The
   default floor of `0.0` keeps everything (a `0.0`-confidence fact is
   not *below* `0.0`).
5. **`--summary`** lazily imports
   `chunkshop.summarizers.lede.summarize` and attaches a
   `max_length=300` summary of the source chunk's text. Only attached
   when a source chunk was resolved. Missing `[lede]` extra raises
   ImportError at search time.
6. **Errors exit non-zero** with a plain message — no traceback.
   `click.ClickException` for anything that goes wrong (bad YAML,
   missing target, embedder/DSN failure), mirroring `search`.

## Output formats

### Default text:

```
1. [0.8912] retry_budget is capped at 3 attempts per request  <- doc-42#7
   summary: The retry policy section caps automatic retries at three…
2. [0.7740] the rollout was paused on 2026-05-01  <- doc-17#2
```

Each line is `N. [score] support_span(<=120 chars)  <- doc_id#seq_num`.
The `<- doc_id#seq_num` is the *source chunk* breadcrumb. With
`--no-summary` (the default) the `summary:` line is omitted. When the
parent chunk can't be resolved the breadcrumb reads `(no source
chunk)`. No matches prints `(no facts matched)`.

### JSON (`--json`):

```json
[
  {
    "fact": {
      "subject": "retry_budget",
      "predicate": "is capped at",
      "object": "3 attempts per request",
      "support_span": "retry_budget is capped at 3 attempts per request",
      "confidence": 0.8912
    },
    "doc_id": "doc-42",
    "chunk": {
      "doc_id": "doc-42",
      "seq_num": 7,
      "text": "The retry policy section caps automatic retries at three attempts…",
      "metadata": {"heading": "Retry policy", "strategy": "consolidation"}
    },
    "score": 0.8912,
    "summary": "The retry policy section caps automatic retries at three…"
  }
]
```

Notes on the shape:

- `fact.support_span` is the fact row's own text (the span the fact was
  distilled from). `fact.confidence` is `null` when the fact carries no
  confidence.
- `chunk` is the resolved source chunk (`doc_id`, `seq_num`, `text`,
  `metadata`) or `null` when unresolved. Its `text` is the chunk's
  `original_content`.
- `summary` is present only when `--summary` was passed *and* a source
  chunk was resolved.
- JSON is serialized with `default=str`, so non-JSON-native values
  (e.g. timestamps) stringify rather than error.

## Errors

| Exit code | Cause |
|-----------|-------|
| 1 (ClickException) | Cell config validation failure, missing/unreachable target, embedder load failure, DSN resolution failure, etc. |

## Example: minimal

```bash
chunkshop fact-search --config memory_kb.yaml --query "retry budget"
```

## Example: high-confidence facts with source summaries, JSON

```bash
chunkshop fact-search \
    --config memory_kb.yaml \
    --query "what limits were decided" \
    --confidence-floor 0.7 \
    --summary \
    --k 5 \
    --json
```

## How it integrates with the pipeline

`fact-search` is the consumer side of the fact-extraction path. For it
to return anything, the ingest must produce `kind='fact'` rows:

```
consolidation (lede / lede_spacy) → kind='fact' rows  → fact-search
                                    (subject/predicate/object,
                                     confidence, source_chunk_seq)
```

Facts and chunks live in the **same** target table. Each fact row
carries `metadata.source_chunk_seq` pointing back at the chunk it was
distilled from, plus the `subject` / `predicate` / `object` /
`confidence` keys this command surfaces. See
[`consolidator-fact-extractors`](consolidator-fact-extractors.md) for
how those modes emit facts.

## Tests proving the contract

- `tests/chunkshop/test_cli_fact_search.py`:
  - basic fact round-trip with breadcrumb resolution
  - `--confidence-floor` drops low-confidence facts but keeps
    `null`-confidence facts
  - missing `source_chunk_seq` → `(no source chunk)` / `chunk: null`
  - `--json` output shape
  - `--summary` attaches a source-chunk summary (skips without `[lede]`)
  - no matches → `(no facts matched)`

## See also

- Reference: [`cli-search`](cli-search.md) — the sibling subcommand;
  excludes facts by default (`--include-facts` to opt back in)
- Reference: [`consolidator-fact-extractors`](consolidator-fact-extractors.md) —
  upstream `consolidation` modes that emit `kind='fact'` rows
- [`docs/hybrid-search.md`](../hybrid-search.md) — hybrid search architecture
