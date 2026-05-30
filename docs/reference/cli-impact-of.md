# `chunkshop impact-of`

**Module**: `chunkshop.cli:impact_of`
**Type**: CLI subcommand
**Ship status**: verified
**Optional extra**: none beyond chunkshop core (psycopg ships in core deps)
**Since**: 2026-05-25 (commit `f5b5cac`, SP-E)

## Purpose

Walk the `<schema>.code_edges` table for callers / callees of a given
fully-qualified name. The edges table is populated by the
`code_relationships` extractor's `finalize()` + `write_edges()` pass
(or by the SP-E runner integration in commit `cd7a013`).

This is the "who calls X / what does X call" graph query — the third
of the three queries that a code-aware chunkshop cell enables (the
other two being find-by-symbol and summarize-symbol, both via
`chunkshop search`).

## Usage

```
chunkshop impact-of --config CFG --fqn FQN [OPTIONS]
```

## Options

| Option              | Type   | Default                | Notes |
|---------------------|--------|------------------------|-------|
| `--config`          | path   | **Required**           | Cell YAML; only `target.dsn` + `target.database_name` are read. |
| `--fqn`             | string | **Required**           | Fully-qualified name to query, e.g. `chunkshop.sources.http.HttpSource.iter_changes_since`. |
| `--depth`           | int    | `1`                    | Number of edge hops to walk. `1 ≤ depth ≤ 10`. |
| `--direction`       | choice | `"callers"`            | `callers` / `callees` / `both`. |
| `--edge-type`       | string | `"CALLS"`              | Edge type to follow: `CALLS` / `INHERITS` / `IMPLEMENTS`. |
| `--edge-kind`       | choice | `None`                 | Optional typed codegraph EdgeKind filter. One of `contains`, `calls`, `imports`, `exports`, `extends`, `implements`, `references`, `type_of`, `returns`, `instantiates`, `overrides`, `decorates`. **ANDs** with `--edge-type` when both are given. Today the extractor only populates `calls` / `extends` / `implements`; the other 9 are reserved. |
| `--confidence`      | float  | `0.7`                  | Minimum edge confidence. Ambiguous (`ambiguous_name`) edges are 0.5 and are filtered out by the default floor; `import_resolved` edges land at 0.9 and pass it. |
| `--project-id`      | string | `cell_name` from YAML  | Project ID scope for the edges table. |
| `--json`            | flag   | off                    | JSON output instead of human-readable tree. |

## Behavior contract

1. **Postgres-only.** Other target types (sqlite, mariadb, clickhouse)
   raise `UsageError` — the recursive-CTE impact query is Postgres
   SQL.
2. **Depth hard-capped at 10.** A malformed CLI call can't ask
   Postgres to walk arbitrary hops. 10 is past any real-world
   impact-of question (call graphs rarely exceed 5 hops in human-
   readable explanations) while bounding worst-case fanout cost.
3. **`project_id` allowlist regex** (`^[A-Za-z0-9_.\-]+$`). Defaults
   to the cell's `cell_name` (matching what the runner stamps on
   edge rows).
4. **Direction `both`** runs two separate one-direction queries and
   concatenates results — same depth and confidence apply to both.
5. **Walks `<schema>.code_edges`** via a recursive CTE. Joins back to
   `<schema>.<chunks_table>` to enrich each hit with `chunk_id`,
   `chunk_seq`, `start_line`, `end_line`, `summary`, `file_path`,
   `language` (when those columns are promoted).
6. **Errors exit non-zero** with a clean message.

## Output formats

### Default text (tree-style):

```
impact-of: chunkshop.sources.http.HttpSource.iter_changes_since
  callers (1 hop):
    [0.9] chunkshop.runner.run_cell    src/chunkshop/runner.py:88
    [0.9] tests.test_http.test_sync    tests/chunkshop/test_http.py:34
  callees (1 hop):
    [0.9] chunkshop.sources.http.HttpSource._crawl   src/chunkshop/sources/http.py:348
    [0.5] urllib.parse.urlparse                       (3 candidates: stdlib match)
```

### JSON (`--json`):

```json
{
  "target": "chunkshop.sources.http.HttpSource.iter_changes_since",
  "project_id": "chunkshop_main",
  "depth": 1,
  "direction": "both",
  "edge_type": "CALLS",
  "edge_kind": null,
  "confidence_floor": 0.7,
  "callers": [
    {
      "fqn": "chunkshop.runner.run_cell",
      "node_id": "node-abc123",
      "confidence": 0.9,
      "hop": 1,
      "file_path": "src/chunkshop/runner.py",
      "start_line": 88,
      "end_line": 142,
      "summary": "Wire all five pipeline stages together…"
    }
  ],
  "callees": [...]
}
```

The top-level JSON echoes back `edge_kind` (the value passed to
`--edge-kind`, or `null` when unset) alongside the existing `edge_type`.
Per-edge **provenance** (`ast` for intra-file, `heuristic` for cross-file
name resolution) is stored on each `code_edges` row and is recoverable
from `evidence.resolution` on the source edges — `intra_file` is `ast`;
`unique_name` / `import_resolved` / `ambiguous_name` are `heuristic`.
(The impact-of result rows surface `evidence` and the FQNs, not a
separate `provenance` column.)

## Errors

| Exit code | Cause |
|-----------|-------|
| 2 (UsageError) | `--depth` outside `[1, 10]`, non-Postgres target, invalid `--project-id`. |
| 1 (ClickException) | YAML invalid, DSN missing, edge table doesn't exist, etc. |

## Example: minimal

```bash
chunkshop impact-of \
    --config repo.yaml \
    --fqn chunkshop.sources.http.HttpSource.iter_changes_since
```

## Example: two-hop both-directions

```bash
chunkshop impact-of \
    --config repo.yaml \
    --fqn chunkshop.sources.http.HttpSource \
    --direction both \
    --depth 2 \
    --json
```

## Example: inheritance graph

```bash
chunkshop impact-of \
    --config repo.yaml \
    --fqn chunkshop.sources.base.IncrementalSource \
    --edge-type INHERITS \
    --depth 3
```

## Example: ambiguous-edge inclusion

```bash
# Allow ambiguous-name resolution edges (0.5 confidence) into the result:
chunkshop impact-of \
    --config repo.yaml \
    --fqn chunkshop.config.Pipeline \
    --confidence 0.5
```

## Example: typed EdgeKind filter

```bash
# AND the typed codegraph EdgeKind on top of --edge-type. Here: only
# `calls` edges (the kind the extractor derives from edge_type=CALLS).
chunkshop impact-of \
    --config repo.yaml \
    --fqn chunkshop.runner.run_cell \
    --edge-type CALLS \
    --edge-kind calls
```

## Prerequisites

For `impact-of` to return results, your ingest must:

1. **Use the `code_relationships` extractor** to produce edges.
2. **Materialize edges to the `code_edges` table** — either via the
   SP-E runner integration (which auto-calls `extractor.finalize()` +
   `write_edges()` after `run_cell`) or via a post-ingest script:
   ```python
   from chunkshop.extractors.code_relationships import (
       write_edges_schema, write_edges,
   )
   write_edges_schema(dsn, schema=schema)
   write_edges(extractor, dsn=dsn, schema=schema, project_id=cell_name)
   ```
3. **Promote useful metadata** to real columns in the chunks table for
   enrichment join:
   ```yaml
   target:
     promote_metadata:
       - {path: fqn,         type: text}   # required for join
       - {path: file_path,   type: text}
       - {path: start_line,  type: int}
       - {path: end_line,    type: int}
       - {path: summary,     type: text}
   ```

## How it integrates with the pipeline

```
symbol_aware → code_relationships → run_cell → write_edges → code_edges table
                                                                    ↓
                                                       chunkshop impact-of
```

For the full recipe, see [`docs/cookbook/code-search.md`](../cookbook/code-search.md).

## Tests proving the contract

- `tests/chunkshop/test_cli_impact_of.py`:
  - depth boundary enforcement (1, 10, 11)
  - direction `callers` / `callees` / `both` matrix
  - confidence floor filters out 0.5 edges by default
  - non-Postgres target → UsageError
  - `project_id` regex enforcement
  - chunk-metadata enrichment via JOIN
  - empty result → "(no edges found)" text path
- Demo: `python/examples/code_search_demo.py`.

## See also

- Reference: [`extractor-code-relationships`](extractor-code-relationships.md)
- Reference: [`chunker-symbol-aware`](chunker-symbol-aware.md)
- Reference: [`cli-search`](cli-search.md) — the sibling subcommand with `--by-symbol`
- [`docs/cookbook/code-search.md`](../cookbook/code-search.md)
