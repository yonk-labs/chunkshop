# Symbol-Aware Code Ingest for chunkshop

**Date:** 2026-05-25
**Status:** in execution
**Spec source:** /Users/matt.yonkovit/yonk-tools/yonk-full-stack-mig-srv (yonk-full-stack-mig-srv survey by Explore agent)
**Tracks:** chunkshop, downstream pg-raggraph

## Goal

Make any ingested git repo (or any code corpus) queryable along three axes that
the current ingest can't serve:

1. **"Find concrete function X"** — exact-match search on symbol name across
   ingested repos; returns the chunk(s) defining X plus their file/line range.
2. **"Summarize file Y / function X"** — per-chunk and per-file natural-language
   summaries land as queryable metadata.
3. **"Cascading impact of X"** — given a function, walk forward (what X calls)
   and backward (who calls X), single-hop and N-hop, across files in the same
   project.

Source corpus is any of: a cloned repo via `FilesSource`, the github connector,
or the gdrive connector pointed at a code drive.

## Decisions (do not re-litigate)

| # | Decision | Rationale |
|---|---|---|
| D1 | **tree-sitter is the parser backbone** (multi-lang). Hand-written regex fallback per language carried over from yonk-full-stack-mig-srv. | Their layer covers Python, Java, Go, TS, JS, Rust, C#, PHP, SQL with the same API. Lifting is days not weeks. |
| D2 | **Code understanding lives in chunkshop's chunker + extractor surfaces**. Graph topology storage lives in pg-raggraph or the consumer's DB. | Per CLAUDE.md / SP-1 D7: chunkshop is a primitive provider, not a graph DB. |
| D3 | **Chunk-per-symbol, not chunk-per-line.** A top-level function or class is one chunk. Methods stay nested under their class by default. | Symbol-bounded chunks are what semantic search wants — line chunks fragment definitions. |
| D4 | **Relationships emit as TWO outputs**: per-chunk metadata (`callees: [...]`) AND a sibling `<schema>.code_edges` table written by the extractor's post-pass. | Per-chunk metadata is enough for "what does X call." Sibling table is what graph consumers need for traversal. |
| D5 | **Cross-file resolution is name-unique-only**, conservative confidence band 0.7–0.95. Ambiguous-name callees are emitted with `confidence=0.5` and `resolved=False`. | yonk-full-stack-mig-srv's posture: false edges are worse than missing edges. Consumers can ramp confidence threshold. |
| D6 | **Per-symbol summaries via lede by default**, optional LLM via the same `chunkshop.summarizers.<name>` plug shape that already exists. | Zero-dep summary works today; LLM is opt-in. |
| D7 | **All four new components ship as opt-in extras** (`chunkshop[code]` umbrella with `tree-sitter-*` per-lang sub-extras). Default install is unchanged. | Heavy tree-sitter language packages should not land in every chunkshop user's wheel. |

## Architecture

```
                                    ┌─────────────────────────────┐
                                    │  chunkshop pipeline (today) │
Source ────► Document ──────────────►   chunker → extractor →     ◄─── pgvector sink
                                    │   embedder                   │
                                    └─────────────────────────────┘
                                                  │
                                                  ▼
                                       ┌────────────────────┐
                                       │  symbol_aware      │   (NEW: D3)
                                       │  chunker           │ ◄─ tree-sitter parse
                                       │                    │    yields one chunk
                                       │                    │    per symbol
                                       └────────────────────┘
                                                  │
                                                  ▼
                              ┌─────────────────────────────────────┐
                              │  code_relationships extractor (NEW: │
                              │  D4)                                 │
                              │  - per-chunk: callees, callers tag   │
                              │  - post-pass: writes code_edges      │
                              │    table                             │
                              └─────────────────────────────────────┘
                                                  │
                                                  ▼
                              ┌─────────────────────────────────────┐
                              │  code_summary extractor (NEW: D6)   │
                              │  - per-chunk lede summary            │
                              │  - per-file rollup                   │
                              └─────────────────────────────────────┘
```

## Component decomposition (parallel-executable)

### SP-A: Foundation (foreground, blocks B/C/D)

`python/src/chunkshop/codeparse/` (new package). Lifts the parsing layer from
yonk-full-stack-mig-srv with chunkshop's import-rewrite + lazy-import discipline.

- `base.py` — Symbol, CallSite, ParseResult pydantic models
- `tree_sitter_wrapper.py` — multi-lang parse driver. Lazy-imports per-language
  tree-sitter package, falls back to regex when the package is missing.
- `langs/python.py`, `langs/java.py`, `langs/go.py`, `langs/typescript.py`,
  `langs/javascript.py` — per-language query bundles
- `langs/regex_fallback.py` — pure-regex fallback (carryover)
- `fqn.py` — `build_fqn(file, name, parent)` — deterministic FQN construction
- `id.py` — `code_symbol_node_id(project, language, file, fqn)` — sha1-derived 16-hex ID
- Tests: per-language parse fixtures + assertions on Symbol + CallSite shapes

### SP-B: `symbol_aware` chunker (parallel after SP-A)

`python/src/chunkshop/chunkers/symbol_aware.py`. Wraps SP-A's `parse_file`,
emits one `Chunk` per top-level symbol with `metadata={symbol_name, fqn,
symbol_type, language, start_line, end_line, parent_name}`.

Config:
```python
class SymbolAwareChunker(_Base):
    type: Literal["symbol_aware"]
    granularity: Literal["function", "class", "module"] = "function"
    include_imports: bool = True   # prepend file imports to embedded_content
    if_oversize: Optional["ChunkerConfig"] = None
    languages: Optional[list[str]] = None   # None = autodetect by extension
```

Tests: each language's fixture round-trips through the chunker and produces the
expected Chunks.

### SP-C: `code_relationships` extractor (parallel after SP-A)

`python/src/chunkshop/extractors/code_relationships.py`. Two phases:

1. **Per-chunk**: for each Chunk produced by `symbol_aware`, attach
   `metadata.callees = [{name, line, snippet, resolved_intra_file: bool}, ...]`
2. **Post-pass**: after all chunks for a cell are processed, walk the global
   call-site set and emit edges into `<target_schema>.code_edges`:
   ```sql
   CREATE TABLE code_edges (
       project_id   text NOT NULL,
       edge_type    text NOT NULL,   -- CALLS | INHERITS | IMPLEMENTS
       src_fqn      text NOT NULL,
       dst_fqn      text NOT NULL,
       src_node_id  text NOT NULL,
       dst_node_id  text NOT NULL,
       confidence   double precision NOT NULL,
       evidence     jsonb,
       PRIMARY KEY (project_id, edge_type, src_node_id, dst_node_id)
   );
   ```

Promote metadata default: `callees` becomes a jsonb column on the chunk table
so consumers can write `WHERE callees @> '[{"name": "find_user"}]'`.

### SP-D: `code_summary` extractor (parallel after SP-A)

`python/src/chunkshop/extractors/code_summary.py`. Per-chunk summary via the
existing `chunkshop.summarizers.<name>` plug. Default backend is `lede`
(extractive, zero-dep when installed). Optional LLM backend via a
`summarizer.callable_path` config (e.g.,
`module: chunkshop.summarizers.openai_brief`).

Output:
- `metadata.summary` (string, 1-3 sentences)
- `metadata.file_summary` (string, on the FIRST chunk of each file only — a
  rollup of all symbol summaries in that file)

Promote metadata default: `summary` becomes a text column.

### SP-E: Integration + CLI (after B/C/D land)

- `python/src/chunkshop/cli.py` — new `chunkshop search --by-symbol NAME`
  flag that adds a `WHERE symbol_name = $1 OR symbol_name LIKE $1` predicate
  to the existing hybrid_search call.
- `python/src/chunkshop/cli.py` — new `chunkshop search --impact-of FQN
  [--depth N]` subcommand. Joins `chunks` to `code_edges` and walks N hops
  from FQN (default 1). Returns: direct callers, direct callees, and (for
  N>1) the transitive set.
- `python/examples/code_search_demo.py` — runnable demo against the freshly-
  ingested ragflow KB (or any cloned repo), shows:
  - find_symbol("BaseConnector") → 3 results across 3 files
  - impact_of("CheckpointedConnector") → 8 callers across the codebase

### Testing requirements

- Each language (Python, Java, Go, TS, JS) has a `tests/fixtures/codeparse/<lang>/`
  fixture exercising: 1 class with 2 methods, 1 free function, 1 cross-symbol call.
- `assert_symbols` helper: each fixture asserts the exact Symbol list + the
  exact CallSite list. Catches both extraction misses and false positives.
- E2E: ingest a tiny sample repo through the full pipeline (symbol_aware +
  code_relationships + code_summary), then run hybrid search by symbol name,
  then run impact query. All three must succeed.
- The existing chunkshop suite must stay green at 595+ passed.

## Execution timeline (parallel)

```
T+0  : SP-A dispatched (foundation)
T+25 : SP-A done (estimated). SP-B/C/D dispatched as 3 parallel background agents
       in separate worktrees (../chunkshop-spb, ../chunkshop-spc, ../chunkshop-spd)
T+45 : SP-B/C/D rendezvous. Merge in order B → C → D.
T+55 : SP-E dispatched (integration + CLI + demo)
T+60 : Synthesis report.
```

## Risks

- **tree-sitter binary build**: language packages ship pre-built wheels for
  common platforms. macOS arm64 + Linux x86_64 are covered. If a wheel isn't
  available on the runner, the install falls through to a source build via Cargo.
- **Cross-file resolution false positives**: D5's "unique-name-only" rule is
  conservative but Java/Go are heavy on overloaded method names. Mitigation:
  emit `confidence=0.5` for ambiguous + tag `resolved=False`. Downstream
  consumers can filter.
- **`code_edges` table growth**: a 10k-symbol repo can emit 50k-150k edges.
  Indexes on `(project_id, src_node_id)` and `(project_id, dst_node_id)` plus
  a partial index on `confidence >= 0.7` are mandatory.

## Out of scope

- Graph traversal beyond `impact_of`'s depth-N hop. Real graph algorithms
  (PageRank, community detection) belong in pg-raggraph.
- LSP integration — the user explicitly didn't ask for that; tree-sitter +
  name-unique resolution is the bar for now.
- Rust port of any of this. Python-only v1, mirror of SP-1 D6.
