# `code_aware` chunker (Python AST)

**Module**: `chunkshop.chunkers.code_aware`
**Type**: Chunker
**Ship status**: verified
**Optional extra**: none (uses stdlib `ast`)
**Since**: 2026-05-25 (commit `9ebf25b`)

## Purpose

Split Python source files at function/class boundaries via the stdlib
`ast` module. Zero runtime dependencies. Each top-level
function/class becomes one chunk; module-level statements (imports,
constants, `__all__`) gather into a leading `module_block` chunk.

For non-Python documents, the chunker delegates to a fallback
(`if_oversize` if set, otherwise `sentence_aware`) so the same cell can
hold a mixed-language corpus without exploding.

For multi-language code (Python + Java + Go + TS + JS), use the
[`symbol_aware`](chunker-symbol-aware.md) chunker instead — it
generalises this one via tree-sitter + a regex fallback.

## Config schema

`chunkshop.config.CodeAwareChunker` (pydantic v2, `extra="forbid"`):

| Field             | Type                          | Default    | Notes |
|-------------------|-------------------------------|------------|-------|
| `type`            | `Literal["code_aware"]`       | **Required** | Discriminator. |
| `max_chars`       | `int`                         | `4000`     | Soft cap. Oversize functions stay whole unless `if_oversize` triggers. |
| `min_chars`       | `int`                         | `100`      | Module-level statements smaller than this still emit as a block. |
| `include_imports` | `bool`                        | `True`     | When True, each chunk's `embedded_content` is prefixed with the file's import block. |
| `language`        | `Literal["python", "auto"]`   | `"auto"`   | `"python"` forces Python parsing; `"auto"` sniffs by file extension. |
| `if_oversize`     | `ChunkerConfig?`              | `None`     | Fallback chunker invoked on oversize chunks AND on non-Python documents. |

## Public API

```python
from chunkshop.chunkers.code_aware import CodeAwareChunker

class CodeAwareChunker:
    def __init__(self, cfg: CodeAwareCfg, build_chunker=None) -> None: ...
    def chunk(self, doc: Document) -> list[Chunk]: ...
```

Construct via `chunkshop.chunkers.load_chunker(cfg)`.

## Behavior contract

1. **Python-or-fallback.** If `language == "python"` or `language ==
   "auto"` and `doc.metadata.path` / `source_path` / `doc.id` ends in
   `.py`, parses with `ast.parse`. Otherwise emits whatever the
   fallback chunker emits.
2. **SyntaxError → single `code_aware_fallback` chunk.** Malformed
   Python emits one chunk containing the whole doc with
   `strategy='code_aware_fallback'` and `node_name='<unparsable>'`.
3. **Module-level statements gather into `module_block` chunks.**
   Imports + constants + `__all__` etc. become one chunk per contiguous
   pre-def block. They precede the def chunks in output order.
4. **Top-level defs (function / async function / class) become one chunk each.**
   Methods inside a class are part of the class chunk — the class body
   is the boundary.
5. **`embedded_content` framing.** When `include_imports=True`, each
   chunk's `embedded_content` is prefixed with the file's import block
   + a `# Definition: <name>` comment so embeddings carry framing
   context. `original_content` stays the raw source segment.
6. **`ast.get_source_segment`** is the raw extractor — preserves
   indentation, comments, blank lines exactly as in the source.
7. **Imports block extraction.** Walks top-level statements; collects
   `Import`/`ImportFrom` nodes that appear before the first def. Skips
   a leading docstring and tolerates module constants between imports.

## Chunk metadata stamped

For def chunks (`function` / `class`):

```python
{
    "strategy": "code_aware",
    "node_type": "function" | "class",
    "node_name": "<symbol_name>",
    "start_line": <int>,
    "end_line": <int>,
}
```

For `module_block` chunks:

```python
{
    "strategy": "code_aware",
    "node_type": "module_block",
    "node_name": "<module>",
    "start_line": <int>,
    "end_line": <int>,
}
```

For unparsable Python:

```python
{
    "strategy": "code_aware_fallback",
    "node_type": "fallback",
    "node_name": "<unparsable>",
}
```

## Inputs

- `Document` with `content` (the Python source text). Optionally
  `metadata.path` / `source_path` for language auto-detect.

## Outputs

- List of `Chunk` objects, one per def + one per module_block + one
  fallback for unparsable. Empty list when `doc.content` is empty.

## Errors

| Exception | When |
|-----------|------|
| (none at chunk time) | All failure modes degrade to a fallback chunk. |
| Indirect | Whatever the fallback chunker raises if you wired one with an invalid config. |

## Example: minimal

```yaml
chunker:
  type: code_aware
```

Behavior: Python files get AST-split; non-Python files fall back to
`sentence_aware` (max_chars=4000).

## Example: realistic

```yaml
chunker:
  type: code_aware
  max_chars: 6000
  include_imports: true
  language: auto
  if_oversize:
    type: sentence_aware
    max_chars: 2000
```

## How it integrates with the pipeline

`CodeAwareChunker` is loaded via `chunkshop.chunkers.load_chunker(cfg)`
on the discriminator `type: code_aware`. The runner feeds it
`Documents` from any `Source`; output is `Chunks` ready for embedding.

For richer code-search behavior (symbol_name + FQN + node_id in the
metadata so `--by-symbol` and `impact-of` work), use the
[`symbol_aware`](chunker-symbol-aware.md) chunker instead. The
`code_aware` chunker is the simpler "Python-only, no extras" option.

## Tests proving the contract

- `tests/chunkshop/test_chunker_code_aware.py`:
  - top-level fn/class produce one chunk each
  - module_block chunks gather imports + constants
  - imports block prefixes `embedded_content` when `include_imports=True`
  - SyntaxError → single fallback chunk
  - Non-Python doc → falls back to default sentence_aware
  - Empty doc → empty chunk list
- Cookbook: [`docs/cookbook/code-aware-chunking.md`](../cookbook/code-aware-chunking.md).
- Example: `python/examples/chunk_python_code.py`.

## See also

- Reference: [`chunker-symbol-aware`](chunker-symbol-aware.md) —
  multi-language successor
- Reference: [`utility-codeparse`](utility-codeparse.md) — the
  underlying parser library
- [`docs/chunkers.md`](../chunkers.md) — chunker tuning guide
- [`docs/cookbook/code-aware-chunking.md`](../cookbook/code-aware-chunking.md)
