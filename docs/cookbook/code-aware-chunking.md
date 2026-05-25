# Code-aware chunking

The `code_aware` chunker splits Python source files at function and class
boundaries instead of mid-statement. It uses the stdlib `ast` module — no
runtime dependencies. Non-Python documents transparently fall back to the
`sentence_aware` chunker (or a different chunker you configure via
`if_oversize`).

## When to pick it

- You're ingesting source code (your own repos, GitHub mirrors, vendor SDKs)
  and want chunks that read like coherent units — one function, one class —
  rather than 300-word windows that bisect signatures.
- You want each chunk's embedding to "know" what library it uses. With
  `include_imports: true` (default) the file's import block is prepended to
  every chunk's `embedded_content`, so a chunk that calls `BeautifulSoup(...)`
  embeds as code that obviously uses `bs4` — even when the import statement
  itself lives 200 lines away.

## When not to

- The corpus is not source code. Use `hierarchy` or `sentence_aware` instead.
- The corpus is non-Python source. `code_aware` falls back to
  `sentence_aware` for those, which works but provides no semantic boundary
  benefit over picking `sentence_aware` directly.

## Minimal config

```yaml
chunker:
  type: code_aware
```

Defaults: `max_chars=4000`, `min_chars=100`, `include_imports=true`,
`language=auto` (sniffs by file extension).

## Knobs

| Field | Default | Meaning |
| --- | --- | --- |
| `max_chars` | `4000` | Soft cap. A single oversize function stays whole unless `if_oversize` is set. |
| `min_chars` | `100` | Floor for small module-level blocks. Below this the block may still be emitted. |
| `include_imports` | `true` | Prefix the file's import block + `# Definition: <name>` to each chunk's `embedded_content`. `original_content` is never touched. |
| `language` | `"auto"` | `"auto"` sniffs `.py` by extension; `"python"` forces the AST path regardless of extension. |
| `if_oversize` | `null` | Fallback chunker for oversize chunks and for non-Python documents. |

## Two text fields, two purposes

Every chunk has both `original_content` (the raw source segment from
`ast.get_source_segment`) and `embedded_content` (what gets vectorized).
With `include_imports: true`:

```
embedded_content = "import os\nimport bs4\n\n# Definition: scrape\ndef scrape(url): ..."
original_content = "def scrape(url): ..."
```

Search results and audit tools that want exact source code use `original_content`.
Embeddings see `embedded_content` with the framing context. Both are stored
by the sink.

## Metadata

Each chunk's `metadata` carries:

- `strategy`: `"code_aware"` (normal) or `"code_aware_fallback"` (malformed Python)
- `node_type`: `"function"`, `"class"`, `"module_block"`, or `"fallback"`
- `node_name`: the function/class name; `"<module>"` for module-level blocks
- `start_line`, `end_line`: 1-indexed line range in the source file

`module_block` collects consecutive top-level statements (imports, constants,
`__all__`, etc.) that precede the first function/class definition. Trailing
module statements after the last def also gather into a module_block.

## Oversize handling

A single function larger than `max_chars` is allowed by default — splitting
mid-function defeats the purpose. If you genuinely need a hard ceiling, wire
a fallback chunker:

```yaml
chunker:
  type: code_aware
  max_chars: 2000
  if_oversize:
    type: sentence_aware
    max_chars: 1500
    min_chars: 50
```

When the AST chunker emits a function over 2000 chars, that one chunk is
re-chunked through `sentence_aware`. The framing `# Definition: ...` prefix is
dropped on the fallback path (because `apply_if_oversize` re-chunks the raw
`original_content` per the shared if_oversize contract).

## Malformed Python

If `ast.parse` raises `SyntaxError`, the chunker logs a `WARN` and emits a
single chunk holding the whole document with `strategy="code_aware_fallback"`.
This lets you ingest a partially-broken corpus without losing files entirely.

## Non-Python files

`auto` mode treats any non-`.py` document as "fall through". The chunker
delegates to `if_oversize` if set, otherwise to a default `sentence_aware`
chunker. This means `code_aware` is safe to use as the chunker for a mixed
corpus — Python files get AST splits, everything else gets sentence-aware
splits.

## Demo

The repo ships `python/examples/chunk_python_code.py` which runs `code_aware`
over chunkshop's own source tree and prints one line per emitted chunk:

```
# chunkers/sentence_aware.py -> 4 chunk(s)
  [ 0] module_block <module>                       lines 1-11   orig=  379c  embed=  726c
  [ 1] function     _split_plain                   lines 14-31   orig=  697c  embed= 1048c
  [ 2] function     _split_prose                   lines 34-51   orig=  799c  embed= 1150c
  [ 3] class        SentenceAwareChunker           lines 54-83   orig= 1113c  embed= 1472c
```

Run it with `uv run python examples/chunk_python_code.py`.
