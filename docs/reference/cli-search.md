# `chunkshop search`

**Module**: `chunkshop.cli:search`
**Type**: CLI subcommand
**Ship status**: verified
**Optional extra**: `chunkshop[lede]` (only for `--return summary+chunks` / `--return summary`)
**Since**: extended 2026-05-25 (added `--by-symbol` in commit `f5b5cac`); extended 0.7.0 (added `--compress` + `--include-facts`)

## Purpose

Hybrid-search a cell's target table by free-text query. Embeds the
query with the cell's configured embedder, runs a hybrid semantic +
full-text search against the cell's target, and prints the results.
The `--by-symbol` flag narrows results to chunks whose `symbol_name`
matches an IN()-list or LIKE-prefix pattern.

## Usage

```
chunkshop search --config CFG --query QUERY [OPTIONS]
```

## Options

| Option              | Type    | Default       | Notes |
|---------------------|---------|---------------|-------|
| `--config`          | path    | **Required**  | Cell YAML config. |
| `--query`           | string  | **Required**  | Free-text query string. |
| `--k`               | int     | `10`          | Number of results to return. |
| `--return`          | choice  | `"chunks"`    | `chunks` / `summary+chunks` / `summary`. |
| `--legs`            | string  | `"semantic,fts"` | Comma-separated retrieval legs. |
| `--vector-metric`   | choice  | `target.vector_metric` | `cosine` / `inner_product` / `l2`. |
| `--where KEY=VAL`   | string  | (none)        | Repeatable. Supports `source=X`, `tags=a,b`, `metadata.k=v`. |
| `--by-symbol NAMES` | string  | (none)        | Comma-separated; trailing `*` enables LIKE prefix-match. |
| `--include-facts`   | flag    | off           | Include agent-memory `kind='fact'` rows (excluded by default). |
| `--compress`        | flag    | off           | Run the `caveman` reducer over the produced summary (only affects `--return summary` / `summary+chunks`). |
| `--json`            | flag    | off           | JSON output instead of text. |

## Behavior contract

1. **Loads cell config + embedder.** Calls
   `chunkshop.embedders.load_embedder(cfg.embedder)`, embeds the query
   once.
2. **Calls `chunkshop.search_common.search(...)`** with the parsed
   options.
3. **`--by-symbol` parsing:** comma-separated names. A name ending in
   `*` (or containing `%`) is a LIKE pattern; the rest are exact
   match. Example: `"BaseConnector,HttpSource*"` becomes
   `(["BaseConnector"], ["HttpSource%"])`. Exact names go into a
   `WHERE symbol_name IN (...)`; the LIKE pattern goes into a
   `WHERE symbol_name LIKE 'HttpSource%'`.
4. **Multiple LIKE patterns** in `--by-symbol` are not fully supported
   in v1 — only the first LIKE prefix becomes a `column_like`
   predicate; additional ones degrade to exact-match
   (`.rstrip("%")`). The CLI doesn't error in this case; the docstring
   warns about it.
5. **Requires `symbol_name` promoted to a real column.** The
   `--by-symbol` predicate hits `symbol_name`, which only exists as a
   real column when the cell's YAML has it in `target.promote_metadata`.
   Without that, no chunks match.
6. **Errors exit non-zero** with a plain message — no traceback noise.
   `click.UsageError` for input validation, `click.ClickException` for
   anything else.
7. **`--return summary` / `summary+chunks`** lazily imports
   `chunkshop.summarizers.lede.summarize`. Missing `[lede]` extra
   raises ImportError at search time.
8. **Facts are excluded by default (0.7.0 behavior change).** When the
   target table holds agent-memory `kind='fact'` rows (emitted by the
   `consolidation` chunker's `lede` / `lede_spacy` modes), normal
   search omits them so they don't pollute chunk results. The CLI
   injects `where["metadata_not"] = {"kind": "fact"}` unless you pass
   `--include-facts` (or already supplied `metadata_not` via
   `--where`). The predicate compiles to
   `(metadata ->> 'kind') IS DISTINCT FROM 'fact'` in `search.py`.
   `IS DISTINCT FROM` (not `<>`) keeps rows that *lack* the key, so
   this is a **no-op for plain chunk tables that carry no `kind`
   key** — only fact-bearing memory tables are affected. To search the
   facts directly (with breadcrumbs), use
   [`chunkshop fact-search`](cli-fact-search.md) instead.
9. **`--compress`** runs the `caveman` reducer
   (`chunkshop.summarizers.caveman.summarize`) over the summary text to
   strip fluff tokens for fewer downstream LLM tokens. It is lazily
   imported and only applied when a summary is actually produced
   (`--return summary` / `summary+chunks`); it is a no-op for
   `--return chunks`.

## Output formats

### Default text:

```
1. [0.8743] doc-42#0  iter_changes_since fetches the URL with…  symbol=iter_changes_since fqn=chunkshop.sources.http.HttpSource.iter_changes_since path=src/chunkshop/sources/http.py
2. [0.7891] doc-17#2  The HttpSource is a depth-bounded…
```

With `--return summary+chunks`:

```
SUMMARY:
The HttpSource crawls URLs with optional depth and conditional GETs…

1. [0.8743] …
```

### JSON (`--json`):

```json
{
  "query": "iter changes since",
  "summary": null,
  "chunks": [
    {
      "doc_id": "doc-42",
      "seq_num": 0,
      "score": 0.8743,
      "text": "def iter_changes_since(self, cursor): …",
      "legs": ["semantic", "fts"],
      "metadata": {
        "symbol_name": "iter_changes_since",
        "fqn": "chunkshop.sources.http.HttpSource.iter_changes_since",
        "path": "src/chunkshop/sources/http.py",
        "summary": "Fetches each URL with conditional headers…"
      }
    }
  ]
}
```

## Errors

| Exit code | Cause |
|-----------|-------|
| 2 (UsageError) | `--by-symbol` provided but empty after parsing. |
| 1 (ClickException) | Cell config validation failure, missing target, embedder load failure, etc. |

## Example: minimal

```bash
chunkshop search --config repo.yaml --query "embed query"
```

## Example: filtered to a symbol family

```bash
chunkshop search \
    --config code_kb.yaml \
    --query "fetch each URL" \
    --by-symbol "HttpSource*" \
    --k 5
```

## Example: hybrid with metadata filter, JSON output

```bash
chunkshop search \
    --config docs_kb.yaml \
    --query "vector index tuning" \
    --where source=postgres_docs \
    --where metadata.section=hnsw \
    --return summary+chunks \
    --legs semantic,fts \
    --vector-metric cosine \
    --k 8 \
    --json
```

## Example: compressed summary, facts included

```bash
chunkshop search \
    --config memory_kb.yaml \
    --query "what did the user decide about retries" \
    --return summary+chunks \
    --compress \
    --include-facts \
    --k 8
```

## How it integrates with the pipeline

`chunkshop search` is the consumer side of an ingested cell. It loads
the cell's YAML, builds the embedder + hybrid query, and emits
results. To make `--by-symbol` work, the cell's chunker must stamp
`metadata.symbol_name` (the `symbol_aware` chunker does this
automatically) AND the cell's target must promote that to a real
column:

```yaml
target:
  promote_metadata:
    - {path: symbol_name, type: text}
```

## Tests proving the contract

- `tests/chunkshop/test_cli_search.py`:
  - basic search round-trip
  - `--by-symbol` IN() variant (exact names)
  - `--by-symbol` LIKE variant (trailing `*`)
  - `--by-symbol` rejection on empty input
  - `--json` output shape
  - `--where` parsing
  - Missing `[lede]` extra → ImportError on `--return summary*`
  - Several baseline tests skip without `[lede]` + `[lede-spacy]` +
    `en_core_web_sm` per `CLAUDE.md`.

## See also

- Reference: [`cli-fact-search`](cli-fact-search.md) — the fact-aware
  read side; queries `kind='fact'` rows with breadcrumbs
- Reference: [`cli-impact-of`](cli-impact-of.md)
- Reference: [`chunker-symbol-aware`](chunker-symbol-aware.md) —
  upstream chunker that stamps `symbol_name`
- [`docs/cookbook/code-search.md`](../cookbook/code-search.md)
- [`docs/hybrid-search.md`](../hybrid-search.md) — hybrid search architecture
