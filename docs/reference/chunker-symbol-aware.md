# `symbol_aware` chunker (multi-language)

**Module**: `chunkshop.chunkers.symbol_aware`
**Type**: Chunker
**Ship status**: verified
**Optional extra**: `chunkshop[code]` — real tree-sitter grammars for all ten languages: Python, Java, Go, TypeScript, JavaScript, Rust, C, C++, C#, and Ruby. Without the extra the regex fallback covers the same ten.
**Since**: 2026-05-25 (commit `64b15bf`, SP-B)

## Purpose

Multi-language code chunker. Splits source files at symbol boundaries
(function / class / method) for any language `chunkshop.codeparse`
understands: Python, Java, Go, TypeScript, JavaScript, Rust, C, C++, C#,
and Ruby — all via real tree-sitter grammars in the `[code]` extra
(regex fallback when the extra is absent). Generalises
[`code_aware`](chunker-code-aware.md) which is Python-only.

Each chunk carries the symbol's name, fully-qualified name, type,
language, and a deterministic `node_id`. This is what makes the
`chunkshop search --by-symbol` filter and the `chunkshop impact-of`
subcommand work: they query on those promoted columns.

Documents the chunker can't parse (no resolvable language, Python with a
syntax error, zero symbols found) fall back to `sentence_aware` so an
ingest pipeline never silently drops a document — each fallback chunk is
tagged `strategy='symbol_aware_fallback'` with a `fallback_reason` for
forensics. Language no longer hinges on a file extension being present:
detection is layered (see [Behavior contract](#behavior-contract)) so a
caller passing a synthetic id / URI with no path still gets symbols.

## Config schema

`chunkshop.config.SymbolAwareChunker` (pydantic v2, `extra="forbid"`):

| Field             | Type                                                   | Default      | Notes |
|-------------------|--------------------------------------------------------|--------------|-------|
| `type`            | `Literal["symbol_aware"]`                              | **Required** | Discriminator. |
| `granularity`     | `Literal["function", "class", "module"]`               | `"function"` | Per-symbol output level — see below. |
| `include_imports` | `bool`                                                 | `True`       | Prepend file's import block to `embedded_content`. |
| `max_chars`       | `int`                                                  | `8000`       | Soft cap before `if_oversize` triggers. |
| `languages`       | `list[str]?`                                           | `None`       | Allowlist by codeparse language tag. None = auto. |
| `language`        | `str?`                                                 | `None`       | Force ONE language for every doc, bypassing detection. Must be a known tag; rejected at config-load otherwise. |
| `if_oversize`     | `ChunkerConfig?`                                       | `None`       | Fallback when a chunk exceeds `max_chars`. |

### Granularity

- **`"function"`** (default): one chunk per top-level function AND per
  top-level class. Methods inside a class are bundled INTO the class
  chunk (the class body is the boundary).
- **`"class"`**: one chunk per top-level class; free top-level functions
  are grouped into a single `module_block` chunk per file.
- **`"module"`**: one chunk per file. The chunk still carries a
  deterministic `node_id` so it joins back to a graph node.

### Languages

When `languages` is set (e.g. `["python", "java"]`), files whose
detected language is outside the allowlist fall through to
`symbol_aware_fallback`. When `None` (default), the chunker accepts any
language codeparse can detect via file extension.

## Public API

```python
from chunkshop.chunkers.symbol_aware import SymbolAwareChunker

class SymbolAwareChunker:
    def __init__(self, cfg: SymbolAwareCfg, build_chunker=None) -> None: ...
    def chunk(self, doc: Document) -> list[Chunk]: ...
```

Construct via `chunkshop.chunkers.load_chunker(cfg)`.

## Behavior contract

1. **Language resolution**, layered most- to least-explicit (chunkshop#69):
   (a) `cfg.language` override; (b) a `doc.metadata['language']` hint —
   an exact tag or extension alias like `"tsx"`/`".ts"`; (c) a path-like
   metadata value (`path`, `source_path`, `file_path`, `filename`, `uri`,
   `url`, … — extension-matched via `regex_fallback.detect_language`);
   (d) a path-shaped `doc.id`; (e) a conservative content heuristic
   (`regex_fallback.detect_language_from_content`) that scores weighted,
   language-distinctive markers across **all ten** supported languages and
   returns a result only on a clear, unambiguous winner (a near-tie or
   prose returns nothing). Only when all five yield nothing does the doc
   fall back with `fallback_reason="unsupported_language"`.
2. **Python syntax-error guard.** Tree-sitter is error-tolerant; the
   chunker explicitly runs `ast.parse` on Python sources first. A
   `SyntaxError` triggers fallback.
3. **Temp-file dispatch.** The chunker hands in-memory `doc.content`
   to `codeparse.parse_file` via a short-lived temp file (so codeparse
   can use its path-based language detection). The temp file is
   `os.unlink`'d in a finally block.
4. **FQN rebuild against logical path.** codeparse returns FQNs based
   on the temp-file path (random per-run). The chunker rebuilds each
   symbol's FQN via `build_fqn(logical_path, name, parent_name)` so
   `node_id`s are stable across runs of the same file.
5. **`embedded_content` framing.** Imports block prepended +
   `# Definition: <name>` comment. `original_content` is the raw source
   slice.
6. **Determinstic `node_id`.** Each chunk's `metadata.node_id` is
   `code_symbol_node_id("default", language, file_path, fqn)`. Same
   recipe as anywhere else in chunkshop — the node_id stamped here
   matches the node_id a downstream graph-store would mint.
7. **Fallback path** runs a fresh `SentenceAwareChunker(max_chars=min(self.max_chars,
   2000))` and tags every chunk:
   ```python
   {**chunker_metadata, "strategy": "symbol_aware_fallback",
    "fallback_reason": "<reason>"}
   ```
   `reason` is one of: `"unsupported_language"`, `"parse_error"`, `"no_symbols"`.
8. **Oversize handling** routes through
   `chunkshop.chunkers._oversize.apply_if_oversize` — same warning,
   same recursion guard, same metadata propagation as other chunkers.

## Chunk metadata stamped

```python
{
    "strategy": "symbol_aware",            # or "symbol_aware_fallback"
    "symbol_name": "iter_changes_since",
    "fqn": "chunkshop.sources.http.HttpSource.iter_changes_since",
    "symbol_type": "method",               # function | class | method | module | module_block | interface
    "start_line": 418,
    "end_line": 434,
    "parent_name": "HttpSource",           # None for top-level symbols
    "language": "python",
    "node_id": "node-abc1234567890def",    # deterministic
}
```

Fallback chunks additionally carry `"fallback_reason"`.

## Inputs

- `Document` with `content`. Optionally `metadata.path` /
  `metadata.source_path` for language detection.

## Outputs

- List of `Chunk` objects, one per symbol (with `granularity=function`),
  or one fallback set when the doc can't be symbol-split.

## Errors

| Exception | When |
|-----------|------|
| (none at chunk time) | Every failure mode falls through to `_fallback_chunks`. |

## Example: minimal

```yaml
chunker:
  type: symbol_aware
```

## Example: realistic with extractors

```yaml
chunker:
  type: symbol_aware
  granularity: function
  include_imports: true
  max_chars: 8000
extractor:
  type: composite
  extractors:
    - type: code_summary
      backend: lede
      file_summary: true
    - type: code_relationships
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: code_kb
  table: chunks
  mode: overwrite
  promote_metadata:
    - {path: symbol_name, type: text}
    - {path: fqn,         type: text}
    - {path: symbol_type, type: text}
    - {path: language,    type: text}
    - {path: summary,     type: text}
    - {path: start_line,  type: int}
    - {path: end_line,    type: int}
```

The `promote_metadata` block surfaces `symbol_name` etc. as real
columns — this is the load-bearing wiring for `chunkshop search
--by-symbol` and `chunkshop impact-of`.

## Languages supported

Every language ships a real tree-sitter grammar in the `[code]` extra and
falls through to the regex extractor when the extra is absent (or a
grammar raises). Symbol kinds: functions + classes + methods universally;
interfaces for Java / TypeScript / C# (and Rust traits); structs / enums
map to `class` for Go / Rust / C / C++; Ruby maps `module` → `class` and
call-detection is best-effort.

| Language    | Parser path                            | Notes |
|-------------|----------------------------------------|-------|
| Python      | tree-sitter-python OR regex            | Requires `[code]` extra for tree-sitter; falls through to regex without it. |
| Java        | tree-sitter-java OR regex              | Constructors currently labeled `symbol_type="method"`. |
| Go          | tree-sitter-go OR regex                | structs / enums map to `class`. |
| TypeScript  | tree-sitter-typescript OR regex        | interfaces extracted as `interface`. |
| JavaScript  | tree-sitter-javascript OR regex        | functions + classes + methods. |
| Rust        | tree-sitter-rust OR regex              | traits → `interface`; structs / enums → `class`. |
| C           | tree-sitter-c OR regex                 | structs / enums → `class`. |
| C++         | tree-sitter-cpp OR regex               | structs / enums → `class`. |
| C#          | tree-sitter-c-sharp OR regex           | interfaces extracted as `interface`. |
| Ruby        | tree-sitter-ruby OR regex              | `module` → `class`; call-detection best-effort. |

## How it integrates with the pipeline

`SymbolAwareChunker` is loaded via `chunkshop.chunkers.load_chunker(cfg)`
on the discriminator `type: symbol_aware`. It pairs naturally with:

- `code_summary` extractor — stamps per-chunk `metadata.summary`.
- `code_relationships` extractor — stamps per-chunk `metadata.callees`
  and produces a `code_edges` table via `finalize() + write_edges()`.
- `chunkshop search --by-symbol` — filters hybrid-search results to
  chunks whose `symbol_name` matches.
- `chunkshop impact-of` — walks the `code_edges` table for callers /
  callees of a given FQN.

See [`docs/cookbook/code-search.md`](../cookbook/code-search.md) for
the full pipeline.

## Tests proving the contract

- `tests/chunkshop/test_chunker_symbol_aware.py`:
  - Python AST + tree-sitter paths across all ten languages (Python,
    Java, Go, TypeScript, JavaScript, Rust, C, C++, C#, Ruby), with the
    regex fallback exercised when the `[code]` extra is absent
  - granularity matrix (function / class / module)
  - FQN rebuild against logical path
  - `node_id` determinism across runs
  - Python syntax error → fallback with `reason="parse_error"`
  - unknown extension → fallback with `reason="unsupported_language"`
  - empty symbol list → fallback with `reason="no_symbols"`
  - `languages` allowlist enforcement
  - imports-block framing
- Demo: `python/examples/code_search_demo.py` (full pipeline against
  a real repo).

## See also

- Reference: [`chunker-code-aware`](chunker-code-aware.md) — Python-only predecessor
- Reference: [`extractor-code-summary`](extractor-code-summary.md)
- Reference: [`extractor-code-relationships`](extractor-code-relationships.md)
- Reference: [`utility-codeparse`](utility-codeparse.md)
- Reference: [`cli-search`](cli-search.md), [`cli-impact-of`](cli-impact-of.md)
- [`docs/cookbook/code-search.md`](../cookbook/code-search.md)
- [`docs/cookbook/code-and-docs-kbs.md`](../cookbook/code-and-docs-kbs.md) — two-KB pattern
