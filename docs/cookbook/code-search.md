# Code search: symbol_aware ingest + impact_of

A chunkshop cell that's pointed at a source-code corpus instead of prose
gives you three new queries that the default text pipeline can't serve:

1. **find by symbol** — "show me the chunk(s) defining `BaseConnector`."
2. **summarize a function/file** — "what does `iter_changes_since` do?"
3. **cascading impact** — "who calls `HttpSource.iter_changes_since`?
   What does `HttpSource` call?"

The three queries are powered by three composable bits already in chunkshop:

| Layer | Provider | What it stamps |
|---|---|---|
| Chunker | `symbol_aware` | one chunk per top-level function or class; `metadata.symbol_name`, `fqn`, `start_line`, `end_line`, `language`, `node_id` |
| Extractor | `code_relationships` | per-chunk `metadata.callees`; finalize() pass writes `<schema>.code_edges` |
| Extractor | `code_summary` | per-chunk `metadata.summary`; optional per-file `metadata.file_summary` |

All three are opt-in. You compose them in one YAML cell.

## Minimal config

```yaml
cell_name: my_code_search

source:
  type: files
  glob: "/path/to/your/repo/**/*.py"
  id_from: stem

chunker:
  type: symbol_aware
  granularity: function          # function | class | module
  include_imports: true          # prepends the file's imports to embedded_content

extractor:
  type: composite
  extractors:
    - type: code_relationships
      # If omitted, edges land in the same schema as the chunks table.
      # Set this when you want a separate "graph" schema.
      # target_schema: my_code_search_graph
    - type: code_summary
      backend: lede               # lede | callable | first_n_sentences
      max_length: 300
      file_summary: true

embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5
  dim: 384

target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: my_code_search
  table: chunks
  mode: overwrite
  source_tag: my_code_search

  # ▼ These are the load-bearing bits for `--by-symbol` and `impact-of`.
  promote_metadata:
    - {path: symbol_name, type: text}
    - {path: fqn,         type: text}
    - {path: symbol_type, type: text}
    - {path: language,    type: text}
    - {path: summary,     type: text}
    - {path: start_line,  type: int}
    - {path: end_line,    type: int}
    - {path: node_id,     type: text}    # joinable from code_edges.{src,dst}_node_id
```

Each `promote_metadata` entry turns a jsonb-metadata key into a real column
on the chunks table. `symbol_name` becomes a `TEXT` column (the predicate
`--by-symbol` filters on), `start_line` / `end_line` become integers (so
`impact-of` can join chunks back to their source location). If you skip
`promote_metadata` the chunker still stamps the metadata — but it sits in
jsonb where you'd have to query it with a `WHERE metadata @> '...'::jsonb`
clause; the CLI filter expects a real column.

### Optional: enforce referential integrity between chunks and code_edges

Promoting `node_id` makes it FK-target-able from `code_edges.src_node_id` /
`dst_node_id`. Add the constraints once the tables exist (run after the
first ingest):

```sql
ALTER TABLE <schema>.chunks
    ADD CONSTRAINT chunks_node_id_unique UNIQUE (node_id);

ALTER TABLE <schema>.code_edges
    ADD CONSTRAINT code_edges_src_fk
    FOREIGN KEY (src_node_id) REFERENCES <schema>.chunks(node_id)
    ON DELETE CASCADE;

ALTER TABLE <schema>.code_edges
    ADD CONSTRAINT code_edges_dst_fk
    FOREIGN KEY (dst_node_id) REFERENCES <schema>.chunks(node_id)
    ON DELETE CASCADE;
```

With these in place, deleting a chunk row auto-deletes any edges that
reference it as source or target — no orphan-edge cleanup script needed.
Skip this if you intentionally want edges to outlive their chunks (e.g.,
when chunks are re-ingested with new node_ids and you want a transitional
period where both old and new edges coexist).

## Ingest

```bash
chunkshop validate --config cell.yaml         # quick schema sanity check
chunkshop ingest   --config cell.yaml         # run the cell
```

The runner detects that the `code_relationships` extractor has a
`finalize()` method, calls it after the per-doc loop, and writes the
edges to `<target.database>.code_edges` automatically. You'll see a line
like `wrote 312 edges to my_code_search.code_edges` in the run log.

If you set `extractor.target_schema` separately, the edges land in that
schema instead — useful when you want a downstream graph store to consume
the edges without touching the chunks table.

## Query: find a symbol

```bash
# Exact match.
chunkshop search --config cell.yaml \
    --query "iterates changes" \
    --by-symbol iter_changes_since

# Multiple symbols (IN-list).
chunkshop search --config cell.yaml \
    --query "checkpoint" \
    --by-symbol HttpSource,GitSource

# Prefix match — trailing star expands to LIKE.
chunkshop search --config cell.yaml \
    --query "embed batch" \
    --by-symbol Base*
```

`--by-symbol` composes with `--query` — the query still scores via
semantic + FTS, the symbol predicate just narrows the pool. The human-
readable output appends `symbol=`, `fqn=`, and `path=` to each hit so you
can jump straight to the source.

## Query: impact-of

```bash
# Direct callers (default direction=callers, depth=1).
chunkshop impact-of --config cell.yaml --fqn pkg.module.HttpSource

# What HttpSource calls.
chunkshop impact-of --config cell.yaml --fqn pkg.module.HttpSource \
    --direction callees

# Both at once.
chunkshop impact-of --config cell.yaml --fqn pkg.module.HttpSource \
    --direction both

# Two hops back (callers of callers).
chunkshop impact-of --config cell.yaml --fqn pkg.module.HttpSource.iter_changes_since \
    --depth 2

# JSON for piping into downstream tooling.
chunkshop impact-of --config cell.yaml --fqn pkg.module.HttpSource \
    --direction both --json
```

`--depth` is hard-capped at 10 to prevent runaway recursive-CTE queries.
The default confidence floor is `0.7`, which is the threshold the
`code_relationships` extractor uses for unique-name resolution. Ambiguous
edges (multiple corpus symbols share a bare name) come out at `0.5` and
are filtered out by default; lower `--confidence` if you want to see them.
When the calling file's imports disambiguate the target, import-aware
narrowing can collapse an otherwise-ambiguous match to a single
higher-confidence edge tagged `resolution="import_resolved"`.

### project_id

The runner stamps each edge with `project_id = cell_name`. `impact-of`
defaults to using the same value. If you have multiple cells writing into
the same `code_edges` table, pass `--project-id` to scope the walk.

## What ends up in the chunks table

A chunk for a top-level Python function looks like::

```text
doc_id           : repo/pkg/sources/http.py
seq_num          : 3
original_content : <the function body, verbatim>
embedded_content : <imports block> + Definition: iter_changes_since + body
embedding        : [384 floats]
metadata         : {
  "strategy":         "symbol_aware",
  "symbol_name":      "iter_changes_since",
  "fqn":              "pkg.sources.http.HttpSource.iter_changes_since",
  "symbol_type":      "method",
  "language":         "python",
  "start_line":       127,
  "end_line":         184,
  "parent_name":      "HttpSource",
  "node_id":          "<sha1 hex>",
  "callees":          [{"name": "_resolve_url", "line": 132, ...}, ...],
  "summary":          "Yields one DocChange per remote object newer than cursor."
}
```

After `promote_metadata` runs, you also get top-level columns
`symbol_name`, `fqn`, `symbol_type`, `language`, `summary`, `start_line`,
`end_line` that you can query directly.

## What ends up in code_edges

```sql
CREATE TABLE my_code_search.code_edges (
    project_id   text NOT NULL,
    edge_type    text NOT NULL,           -- CALLS | INHERITS | IMPLEMENTS
    src_fqn      text NOT NULL,
    dst_fqn      text NOT NULL,
    src_node_id  text NOT NULL,
    dst_node_id  text NOT NULL,
    confidence   double precision NOT NULL,
    evidence     jsonb,
    PRIMARY KEY (project_id, edge_type, src_node_id, dst_node_id)
);
```

Indexes are created on `(project_id, src_node_id)`,
`(project_id, dst_node_id)`, and a partial index on `confidence >= 0.7`.

## Languages supported today

The `symbol_aware` chunker + `code_relationships` extractor share a
tree-sitter-based parser. All ten supported languages use real
tree-sitter grammars shipped in the `[code]` extra — Python, Java, Go,
TypeScript, JavaScript, Rust, C, C++, C#, and Ruby:

- Python (full)
- Java (full)
- Go (full)
- TypeScript / JavaScript (full)
- Rust (full)
- C / C++ (full)
- C# (full)
- Ruby (full)

`regex_fallback` is only the safety net when the `[code]` extra is
absent. Only file types with **no** codeparse support (i.e. not in the
list above) fall back to a `sentence_aware` chunker tagged with
`strategy="symbol_aware_fallback"`, so an ingest never silently drops a
document. The `code_relationships` extractor emits no edges for the
fallback chunks.

## End-to-end runnable demo

`python/examples/code_search_demo.py` ingests the chunkshop repo into a
throwaway schema and exercises each of the three queries. See the script's
docstring for usage. It's a good smoke-check that your Postgres + extras
install is wired correctly.
