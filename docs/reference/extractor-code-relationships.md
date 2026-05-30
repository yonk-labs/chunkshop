# `code_relationships` extractor

**Module**: `chunkshop.extractors.code_relationships`
**Type**: Extractor
**Ship status**: verified
**Optional extra**: inherits `chunkshop[code]` from `codeparse` (the regex fallback still works without it)
**Since**: 2026-05-25 (commit `be86e60`, SP-C)

## Purpose

Extract code-relationship edges from source-code chunks. Two-phase:

- **Per-chunk (`extract`)**: re-parses chunk content via
  `chunkshop.codeparse.parse_text`, registers symbols, captures call
  sites + class inheritance, and stamps `metadata.callees` on the
  chunk.
- **Corpus-level (`finalize`)**: walks accumulated call sites and class
  declarations, resolves them to FQNs against the global symbol map,
  and returns a deterministic list of edges (`CALLS`, `INHERITS`,
  `IMPLEMENTS`) with confidence bands.

Together with the `symbol_aware` chunker, this is what powers
`chunkshop impact-of`.

## Config schema

`chunkshop.config.CodeRelationshipsExtractor` (pydantic v2,
`extra="forbid"`):

| Field                         | Type                            | Default      | Notes |
|-------------------------------|---------------------------------|--------------|-------|
| `type`                        | `Literal["code_relationships"]` | **Required** | Discriminator. |
| `target_schema`               | `str?`                          | `None`       | Optional schema for the `code_edges` table; consumed by `write_edges()` helper, not the runner. |
| `unique_match_confidence`     | `float`                         | `0.9`        | `[0, 1]`. Emitted when exactly one symbol matches a callee name. |
| `ambiguous_match_confidence`  | `float`                         | `0.5`        | `[0, 1]`. Emitted when multiple symbols share the callee name. |

## Public API

```python
from chunkshop.extractors.code_relationships import (
    CodeRelationshipsExtractor,
    write_edges,
    write_edges_schema,
)
from chunkshop.extractors.result import ExtractResult

class CodeRelationshipsExtractor:
    accepts_chunk_context: bool = True   # signals to runner to pass source_path/language

    def __init__(self, cfg: CodeRelationshipsExtractorCfg) -> None: ...

    def extract(
        self,
        text: str,
        *,
        source_path: Optional[str] = None,
        language: Optional[str] = None,
    ) -> ExtractResult: ...

    def finalize(self, *, project_id: str = "default") -> list[dict]: ...


def write_edges_schema(dsn: str, *, schema: str) -> None: ...
def write_edges(
    extractor: CodeRelationshipsExtractor,
    *,
    dsn: str,
    schema: str,
    project_id: str = "default",
) -> int: ...
```

## Behavior contract

### Phase 1 — `extract(text, *, source_path, language)`

1. **Language auto-detect** if `language` is None — cheap heuristics
   over text shape (Java's `class X` declaration, Go's `func X`, JS's
   `function X`, Python's `def`/`class`).
2. **Parse via `codeparse.parse_text`** with the detected language.
3. **Register every symbol** in the global symbol map keyed by `fqn`.
4. **Class inheritance regexes** (Python `class X(Base):`, Java
   `class X extends Y implements Z`) capture INHERITS / IMPLEMENTS
   edges that codeparse's `ParseResult` doesn't surface.

> **Caller attribution (Feature B).** A call site inside a *nested*
> function attributes to the **outermost** emitted symbol — the one the
> chunker actually stamped — rather than the inner closure. Calls at
> module scope (no enclosing emitted function) produce no orphan `CALLS`
> edge. For Python, a symbol's span is widened to include its decorator
> lines, so `@decorator` rows stay attached to the symbol they decorate.
5. **Per-chunk metadata** stamps `callees` — a list of:
   ```python
   {
       "name": "<callee_name>",
       "line": <int>,
       "snippet": "<source line>",
       "resolved_intra_file": <bool>,
   }
   ```
6. **Tags are always empty.** Call info is structured metadata, not
   flat-string tags.
7. **`accepts_chunk_context = True`** signals to the runner to pass
   `source_path=` / `language=` from the chunk's metadata. When the
   runner calls `extract(text)` positionally (no kwargs), the extractor
   auto-detects.

### Phase 2 — `finalize(*, project_id)`

1. **Intra-file pre-resolved calls** get an edge at the unique band
   (`unique_match_confidence=0.9`) with `resolution="intra_file"` so
   `impact-of` works inside a single file. These are AST-direct, so they
   keep `provenance="ast"`.
2. **Unique name match** (exactly one corpus symbol shares the callee
   name) → one edge at `unique_match_confidence=0.9` with
   `resolution="unique_name"`.
3. **Import-resolved match** → when a name matches candidates in more
   than one file, the resolver narrows by the caller file's imports
   (the candidate file's *stem* must appear in the caller's import
   tokens). If **exactly one** candidate is import-supported, a single
   edge is emitted at `unique_match_confidence=0.9` with
   `resolution="import_resolved"` and the full `candidates` list in
   `evidence`.
4. **Ambiguous name match** → if **zero or two-or-more** candidates are
   import-supported, fan out one edge per candidate at
   `ambiguous_match_confidence=0.5` with `resolution="ambiguous_name"`.
   Each carries the full `candidates` list in `evidence`.
5. **Zero matches** (external library call) → no edge emitted.
6. **Provenance.** Intra-file (`intra_file`) edges are AST-direct and
   carry `provenance="ast"`. The three cross-file kinds (`unique_name`,
   `import_resolved`, `ambiguous_name`) are name-heuristic resolutions
   and carry `provenance="heuristic"`, so a future stack-graphs resolver
   (`scip`) stays distinguishable. The same import-aware narrowing
   applies to INHERITS / IMPLEMENTS class edges.
7. **Deterministic output.** Sorted by
   `(edge_type, src_fqn, dst_fqn)`. Two `finalize()` calls return the
   same list, comparable with `==`.
8. **Idempotent.** Calling `finalize()` twice doesn't mutate accumulated
   state.

Returned edge shape:

```python
{
    "edge_type": "CALLS" | "INHERITS" | "IMPLEMENTS",
    "edge_kind": "calls" | "extends" | "implements",  # 12-value codegraph EdgeKind, derived from edge_type
    "src_fqn": "<caller fqn>",
    "dst_fqn": "<callee fqn>",
    "src_node_id": "node-<sha1[:16]>",
    "dst_node_id": "node-<sha1[:16]>",
    "confidence": 0.9 | 0.5,
    "evidence": {
        "line": <int>,
        "snippet": "<line text>",
        "resolution": "intra_file" | "unique_name" | "import_resolved" | "ambiguous_name",
        "candidates": [...],   # present for import_resolved + ambiguous
    },
    "provenance": "ast" | "heuristic",   # CHECK also reserves 'scip'
    "provenance_metadata": {},           # jsonb, defaults to {}
}
```

### Module-level helpers

`write_edges_schema(dsn, *, schema)` — idempotent DDL for
`<schema>.code_edges`. Creates the table + 5 indexes (incl. a partial
index on `confidence >= 0.7`, plus `edge_kind` and `provenance`
indexes). Safe to re-run.

`write_edges(extractor, *, dsn, schema, project_id)` — calls
`extractor.finalize(project_id=project_id)` and writes the edges via
`INSERT … ON CONFLICT DO UPDATE`. Returns rows submitted.

### Why a separate `write_edges` rather than inline in `finalize()`?

Two reasons:

1. Keeps the extractor pure-CPU and testable without Postgres.
2. Lets the consumer batch edges across multiple cells before writing
   — a per-cell write would double-insert when two cells share a
   corpus.

## Schema written by `write_edges_schema`

```sql
CREATE TABLE <schema>.code_edges (
    project_id text NOT NULL,
    edge_type text NOT NULL,
    src_fqn text NOT NULL,
    dst_fqn text NOT NULL,
    src_node_id text NOT NULL,
    dst_node_id text NOT NULL,
    confidence double precision NOT NULL,
    evidence jsonb,
    edge_kind text NOT NULL DEFAULT 'references'
        CHECK (edge_kind IN ('contains','calls','imports','exports',
                             'extends','implements','references',
                             'type_of','returns','instantiates',
                             'overrides','decorates')),
    provenance text NOT NULL DEFAULT 'ast'
        CHECK (provenance IN ('ast', 'scip', 'heuristic')),
    provenance_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (project_id, edge_type, src_node_id, dst_node_id)
);
CREATE INDEX code_edges_src_idx ON … (project_id, src_node_id);
CREATE INDEX code_edges_dst_idx ON … (project_id, dst_node_id);
CREATE INDEX code_edges_confident_idx ON …
    (project_id, confidence) WHERE confidence >= 0.7;
CREATE INDEX code_edges_kind_idx ON … (project_id, edge_kind);
CREATE INDEX code_edges_provenance_idx ON … (project_id, provenance);
```

`edge_kind` defaults to `'references'` so a pre-CS-2 row from an older
client still satisfies `NOT NULL`; the extractor's `write_edges` path
always supplies an explicit value via `edge_type_to_kind`. `provenance`
defaults to `'ast'` so pre-CS-5 rows backfill correctly; the `'scip'`
value is reserved for a future stack-graphs resolver.

## Inputs

- Chunk text from `symbol_aware` (or any code-shaped text).
- Optional `source_path` + `language` kwargs (the runner passes these
  when the extractor sets `accepts_chunk_context = True`).

## Outputs

- Per-chunk: `ExtractResult(tags=[], metadata={"callees": [...]})`.
- Corpus-level (via `finalize`): sorted list of edge dicts.

## Errors

| Exception | When |
|-----------|------|
| (none at extract) | Unknown language returns `ExtractResult(tags=[], metadata={"callees": []})` rather than raising. |
| `ValueError` | At `write_edges_schema` / `write_edges` time — invalid `schema` identifier (not `^[a-z_][a-z0-9_]*$`). |
| `psycopg.errors.*` | At write time — bad DSN, missing target schema. |

## Example: minimal

```yaml
extractor:
  type: code_relationships
```

The runner will stamp `metadata.callees` on each chunk. To get the
`code_edges` table, the consumer must call `write_edges` after
`run_cell`:

```python
from chunkshop.runner import run_cell
from chunkshop.extractors.code_relationships import (
    write_edges_schema, write_edges,
)

# … inside your ingest script:
extractor = run_cell.extractor   # the extractor instance the runner held
write_edges_schema(dsn, schema=schema)
write_edges(extractor, dsn=dsn, schema=schema, project_id="my_project")
```

The SP-E runner integration that auto-calls `extractor.finalize()` +
`write_edges` shipped in commit `cd7a013`. See the runner source for
the exact hook.

## Example: realistic — composite extractor

```yaml
extractor:
  type: composite
  extractors:
    - type: code_summary
      backend: lede
    - type: code_relationships
      unique_match_confidence: 0.9
      ambiguous_match_confidence: 0.5
```

## How it integrates with the pipeline

`CodeRelationshipsExtractor` is loaded via
`chunkshop.extractors.load_extractor(cfg)` on the discriminator `type:
code_relationships`. The runner feeds it chunk text (post-chunker,
pre-embed). With `accepts_chunk_context=True`, it also receives the
chunk's `source_path` + `language` so the FQNs the extractor produces
match the FQNs the chunker stamped.

The full code-search workflow:

```
symbol_aware → code_relationships.extract (per-chunk) → embed → sink
                              ↓
              finalize() + write_edges() → code_edges table
                              ↓
              chunkshop impact-of --fqn …
```

## Tests proving the contract

- `tests/chunkshop/test_extractor_code_relationships.py`:
  - per-chunk `callees` metadata stamped
  - unique-name resolution → 0.9 edges
  - ambiguous-name resolution → 0.5 edges per candidate
  - external calls (no match) → no edge
  - INHERITS edges from `class X(Base):` (Python)
  - IMPLEMENTS edges from `class X implements Y, Z` (Java)
  - `finalize` determinism + idempotence
  - `code_symbol_node_id` recipe match with the chunker's node_id
- `tests/chunkshop/test_code_edges_write.py`:
  - `write_edges_schema` idempotent DDL
  - `write_edges` INSERT … ON CONFLICT DO UPDATE round-trip
  - `_validate_ident` rejects unsafe schema names

## See also

- Reference: [`chunker-symbol-aware`](chunker-symbol-aware.md)
- Reference: [`extractor-code-summary`](extractor-code-summary.md)
- Reference: [`utility-codeparse`](utility-codeparse.md) —
  `parse_text`, `build_fqn`, `code_symbol_node_id`
- Reference: [`cli-impact-of`](cli-impact-of.md)
- [`docs/cookbook/code-search.md`](../cookbook/code-search.md)
