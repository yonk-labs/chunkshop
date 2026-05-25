# Comments as docs

Source-code comments carry **rationale** — the "why" behind a value, a
decision, a workaround. Examples that surface this:

- `# subprocess isolation because ONNX Runtime has process-global state`
- `# /* deprecated: use Bar instead */`
- `# /// Returns None when the cache hit is stale — see issue #42.`

If you ingest the same files with the ``symbol_aware`` chunker, every
one of those lines lands in the SAME chunk as the surrounding code.
That's the right move for "find the function that does X" — it's the
wrong move for "why does this code do X". The vector for a 50-line
function body is dominated by code tokens; the four-word rationale at
its head gets averaged out.

The ``comment_extracts`` source closes this gap by mining comments out
of source files and emitting them as standalone Documents, ready for a
prose chunker / extractor / embedder.

## When this pattern earns its keep

- Codebases where contributors actually write prose comments and
  docstrings, not just JSDoc / sphinx scaffolding.
- Retrieval queries that lean toward "why" or "how did we" rather than
  "where is X defined".
- Bakeoff benchmarks where rationale-in-comments shows up in gold
  questions (the failure mode we wanted to close).

## When it doesn't

- Binary files, machine-generated code with sparse comments, vendored
  third-party code (the noise outweighs the signal).
- Languages this module doesn't yet know how to parse — anything
  outside the table below.
- Codebases that already inline rationale in markdown (ADRs, design
  docs). For those, ``kb_docs`` already covers you.

## Per-language coverage

| Language     | Line          | Block          | Doc       | Source          |
|--------------|---------------|----------------|-----------|-----------------|
| python       | `#` (grouped) | —              | tokenize+ast `"""..."""` on module/class/function |
| java         | `//` (grouped) | `/* */`        | `/* */` (JavaDoc lives in block form) | regex |
| javascript   | `//` (grouped) | `/* */`        | `/** */` (JSDoc lives in block form)  | regex |
| typescript   | `//` (grouped) | `/* */`        | `/** */` (TSDoc lives in block form)  | regex |
| go           | `//` (grouped) | `/* */`        | godoc lives in line form, comes through grouped | regex |
| rust         | `//` (grouped) | `/* */`        | `///` / `//!` doc comments — emitted with `kind="block"`, extra `/` / `!` stripped | regex |
| c / cpp      | `//` (grouped) | `/* */`        | (Doxygen lives in block form)         | regex |
| sql          | `--` (grouped) | `/* */`        | —         | regex |
| shell        | `#` (grouped)  | —              | —         | regex |

"Grouped" = consecutive comment lines collapse to one ``CommentBlock`` —
the natural unit when someone writes a paragraph as a stack of `#`
lines. Block comments (`/* ... */`) always stand alone.

The Python path uses stdlib ``tokenize`` + ``ast`` so docstrings get a
specific ``kind="docstring"`` tag and the qualified symbol name
(``Class.method``) lands in metadata. Everything else is regex with a
small state machine that tracks string-literal context so ``//`` inside
a string doesn't get picked up.

## Config

```yaml
source:
  type: comment_extracts
  glob: "src/**/*.py"
  # Optional language allowlist; omitted = autodetect by extension.
  languages: [python]
  # Drop comment blocks shorter than this many characters.
  min_chars: 20
  # How adjacent comments combine:
  #   block    (default) — consecutive comment lines become one Document
  #   per_line          — explode multi-line blocks into one Document per line
  #   per_file          — one Document per file, blocks joined by "\n\n"
  granularity: block
  # Include Python module/class/function docstrings? Default true.
  include_docstrings: true
  # Drop shebangs, encoding decls, `# noqa`, `// @ts-ignore`,
  # `//go:build`, etc. Default true.
  skip_pragmas: true
```

The cell that consumes this source is otherwise unremarkable — pair it
with a prose chunker (``sentence_aware``), prose extractors
(``lang_detect``, ``rake_keywords``), and your sink:

```yaml
cell_name: kb_comments
source:
  type: comment_extracts
  glob: "src/**/*.py"
  min_chars: 40
chunker:
  type: sentence_aware
  min_chars: 100
  max_chars: 800
extractor:
  type: composite
  extractors:
    - type: lang_detect
    - type: rake_keywords
      top_k: 6
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target:
  type: postgres
  dsn_env: PG_DSN
  database: my_schema
  table: kb_comments
  mode: create_if_missing
  source_tag: comments
  promote_metadata:
    - { path: language,    type: text }
    - { path: source_path, type: text }
    - { path: kind,        type: text }
    - { path: start_line,  type: int }
```

## Document shape

Per-block / per-line granularity:

```python
Document(
    id="src/foo.py::comment::42",
    content="why batch_size=64: ORT inference latency knee at 64",
    title="foo.py comments at line 42",
    metadata={
        "source_path": "src/foo.py",
        "start_line": 42,
        "end_line": 44,
        "language": "python",
        "kind": "line",          # "line" | "block" | "docstring"
        "symbol": None,          # "Class.method" for python docstrings
    },
)
```

Per-file granularity collapses all blocks into one Document with
``block_count`` / ``first_line`` / ``last_line`` in metadata.

## Worked example

Mine chunkshop's own repo, count Documents, peek at a few:

```python
from chunkshop.config import CommentExtractsSource
from chunkshop.sources.comment_extracts import CommentExtractsSource as Src

src = Src(
    CommentExtractsSource(
        type="comment_extracts",
        glob="src/chunkshop/**/*.py",
        min_chars=20,
    )
)
docs = list(src.iter_documents())
print(f"{len(docs)} comment Documents")
for d in docs[:3]:
    print(d.metadata["kind"], "->", d.content[:80])
```

Against the v0.6 chunkshop source tree this yields ~800 Documents —
roughly half line-comment blocks, half docstrings. The hit rate for
"why" queries against ``kb_comments`` is the difference between
"close-but-not-quite function-body chunks" and "the actual rationale
sentence".

## See also

- [code-and-docs-kbs.md](code-and-docs-kbs.md) — the two-KB pattern
  this extends to three KBs.
- [code-aware-chunking.md](code-aware-chunking.md) — the
  ``symbol_aware`` chunker that this complements (not replaces).
- [file-parsing.md](file-parsing.md) — how the ``files`` source
  dispatches to per-extension parsers; ``comment_extracts`` is the
  parallel pattern for comments specifically.
