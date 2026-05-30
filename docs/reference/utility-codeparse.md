# `chunkshop.codeparse` — symbol-aware code parsing primitives

**Module**: `chunkshop.codeparse`
**Type**: Utility — primitives for code-symbol extraction
**Ship status**: verified
**Optional extra**: `chunkshop[code]` (real tree-sitter grammars for all ten languages: Python, Java, Go, TypeScript, JavaScript, Rust, C, C++, C#, and Ruby). Without it the regex fallback still handles all ten supported languages.
**Since**: 2026-05-25 (commit `89cce16`, SP-A)

## Purpose

Vendor-neutral, multi-language code parser. SP-A's deliverable —
foundation for the `symbol_aware` chunker (SP-B), the
`code_relationships` extractor (SP-C), and the `code_summary` extractor
(SP-D).

Importing `chunkshop.codeparse` does NOT pull in tree-sitter — the
heavy native wheels stay dormant until you actually parse something.
When the optional `[code]` extra isn't installed, `parse_file` /
`parse_text` fall through to a regex extractor that covers all ten
supported languages with reduced precision but never zero coverage.

## Public API

```python
from chunkshop.codeparse import (
    parse_file,           # path -> ParseResult
    parse_text,           # str -> ParseResult (kwarg-only)
    Symbol,               # pydantic model
    CallSite,             # pydantic model
    ParseResult,          # pydantic model
    build_fqn,            # (file_path, name, parent) -> str
    code_symbol_node_id,  # (project_id, language, file_path, fqn) -> str
)
```

### `parse_file(path, language=None, *, project_id="default")`

```python
def parse_file(
    path: Path | str,
    language: Optional[str] = None,
    *,
    project_id: str = "default",
) -> ParseResult: ...
```

- `language` is one of `{"python", "java", "go", "typescript",
  "javascript", "rust", "c", "cpp", "csharp", "ruby"}` or `None`
  (auto-detect by suffix).
- Unknown language → empty `ParseResult` (not an error).
- File-disappears / read failure → empty `ParseResult` with `language`
  set.
- Never raises on a missing tree-sitter package, malformed UTF-8, or a
  parser crash. Every failure mode falls through to the regex
  extractor.

### `parse_text(content, *, language, file_path="<text>", project_id="default")`

```python
def parse_text(
    content: str,
    *,
    language: str,
    file_path: str = "<text>",
    project_id: str = "default",
) -> ParseResult: ...
```

In-memory companion to `parse_file`. `language` is mandatory (no path
to detect from). Used by `code_relationships` extractor, which
receives chunk text from the runner and needs to re-parse without
round-tripping through a tempfile.

### `Symbol` (pydantic v2 model)

```python
class Symbol(BaseModel):
    name: str
    fqn: str
    symbol_type: str   # "function" | "class" | "method" | "interface" | "module"
    line_start: int    # 1-based, inclusive
    line_end: int      # 1-based, inclusive
    parent_name: Optional[str] = None
```

`parent_name` is set on methods nested inside a class; `None` for
top-level functions and classes.

### `CallSite` (pydantic v2 model)

```python
class CallSite(BaseModel):
    caller_node_id: str           # code_symbol_node_id of the enclosing function
    callee_name: str
    line: int
    snippet: str
    parser: str                   # "tree-sitter" | "regex"
    resolved_intra_file: bool = False
```

`resolved_intra_file=True` means the per-language extractor already
matched the callee to a sibling symbol in the same file — cross-file
resolvers should skip these.

### `ParseResult` (pydantic v2 model)

```python
class ParseResult(BaseModel):
    symbols: list[Symbol]         = []
    call_sites: list[CallSite]    = []
    imports: list[str]            = []
    language: Optional[str]       = None
    parser: Optional[str]         = None   # "tree-sitter" | "regex"
```

The schema is frozen for v1. `raw_tree` is intentionally omitted —
tree-sitter Tree objects aren't pydantic-serializable, and downstream
consumers only need the structured lists. Re-parse if a future feature
needs the raw tree.

### `build_fqn(file_path, symbol_name, parent_name) -> str`

```python
def build_fqn(
    file_path: str,
    symbol_name: str,
    parent_name: Optional[str],
) -> str: ...
```

Concatenates: last 3 path components (extension stripped) + parent +
symbol. Examples:

```python
build_fqn("/a/b/c.py", "f", None) == "a.b.c.f"
build_fqn("c.py", "f", None)      == "c.f"
build_fqn("/a/b/c.py", "g", "C")  == "a.b.c.C.g"
```

Three components is enough to disambiguate in practice. Deep paths
collapse to their last 3 parts — that's intentional. Downstream
consumers that want project-relative paths should pass the relative
path in, not the absolute path.

### `code_symbol_node_id(project_id, language, file_path, fqn) -> str`

```python
def code_symbol_node_id(
    project_id: str,
    language: str,
    file_path: str,
    fqn: str,
) -> str: ...
```

Returns `"node-" + sha1(project_id:language:file_path:fqn)[:16]`.
Deterministic: same inputs → same ID. That property is what makes the
upsert path in a downstream sink (`ON CONFLICT (id) DO UPDATE`)
idempotent: re-running ingest against the same project doesn't
multiply rows.

The 16-hex truncation gives 64 bits of collision resistance — plenty
for any single project, short enough to land in URLs.

## Behavior contract

1. **Lazy tree-sitter import.** Each language module (`python`, `java`,
   `go`, `typescript`, `javascript`, `rust`, `c`, `cpp`, `csharp`,
   `ruby`) imports its tree-sitter package inside its first `parse()`
   call.
2. **Universal regex fallback.** Tree-sitter package missing OR
   grammar bug OR any exception → falls through to
   `regex_fallback.extract_with_regex(...)`. Every supported language
   always returns a non-empty `ParseResult` for a non-empty file with
   recognisable declarations.
3. **No exceptions from `parse_file` / `parse_text`** for parser
   failures. Only real I/O errors propagate (rare — file vanishing
   between detection and read).
4. **FQNs are deterministic** — `build_fqn` is a pure function of its
   inputs.
5. **Node IDs are deterministic** — `code_symbol_node_id` is a SHA1
   over a colon-separated key.
6. **`Symbol`, `CallSite`, `ParseResult` use `extra="forbid"`.** A
   typo'd field in a dict is a bug — silently swallowing it would mask
   language-bundle output drift.

## Language coverage

All ten languages ship a real tree-sitter grammar in the `[code]` extra.
The regex fallback covers the same ten when the extra is absent (or a
grammar raises).

| Language    | tree-sitter (with `[code]`)      | Regex fallback (always) |
|-------------|-----------------------------------|--------------------------|
| Python      | `tree-sitter-python` 0.21+        | Yes |
| Java        | `tree-sitter-java` 0.21+          | Yes |
| Go          | `tree-sitter-go` 0.21+            | Yes |
| TypeScript  | `tree-sitter-typescript` 0.21+    | Yes |
| JavaScript  | `tree-sitter-javascript` 0.21+    | Yes |
| Rust        | `tree-sitter-rust` 0.23+          | Yes |
| C           | `tree-sitter-c` 0.23+             | Yes |
| C++         | `tree-sitter-cpp` 0.23+           | Yes |
| C#          | `tree-sitter-c-sharp` 0.23+       | Yes |
| Ruby        | `tree-sitter-ruby` 0.23+          | Yes |

## Errors

| Exception | When |
|-----------|------|
| (none from `parse_file` / `parse_text`) | All parser failures fall through to regex. Only real OS I/O errors propagate. |

## Example: parse one Python file

```python
from chunkshop.codeparse import parse_file

result = parse_file("/path/to/module.py")
for sym in result.symbols:
    print(f"{sym.symbol_type:<10} {sym.fqn}  L{sym.line_start}-{sym.line_end}")
```

## Example: parse in-memory + build node IDs

```python
from chunkshop.codeparse import parse_text, code_symbol_node_id

src = "def foo(): pass\nclass C: pass"
result = parse_text(src, language="python", file_path="my/mod.py")

for sym in result.symbols:
    nid = code_symbol_node_id(
        project_id="my_proj",
        language=result.language,
        file_path="my/mod.py",
        fqn=sym.fqn,
    )
    print(sym.fqn, "->", nid)
```

## Example: deterministic re-ingest

```python
from chunkshop.codeparse import build_fqn, code_symbol_node_id

# Two ingests, same inputs:
fqn1 = build_fqn("src/a.py", "foo", None)
fqn2 = build_fqn("src/a.py", "foo", None)
assert fqn1 == fqn2 == "src.a.foo"

id1 = code_symbol_node_id("proj", "python", "src/a.py", fqn1)
id2 = code_symbol_node_id("proj", "python", "src/a.py", fqn2)
assert id1 == id2
```

## How it integrates with the pipeline

Three downstream consumers, all in SP-B through SP-D:

- [`chunker-symbol-aware`](chunker-symbol-aware.md) calls `parse_file`
  via a temp file.
- [`extractor-code-relationships`](extractor-code-relationships.md)
  calls `parse_text` on chunk bodies.
- [`extractor-code-summary`](extractor-code-summary.md) doesn't call
  codeparse directly but uses the chunk's `symbol_type` /
  `start_line` metadata (which symbol_aware stamped using codeparse).

The same `code_symbol_node_id` recipe is used by chunkshop and by any
downstream graph-store (`pg-raggraph` uses the same recipe per
SP-A's spec) so chunk IDs and graph node IDs interoperate without an
extra mapping table.

## Tests proving the contract

- `tests/chunkshop/test_codeparse_*`:
  - `parse_file` Python tree-sitter path (when `[code]` extra
    available)
  - regex fallback for all ten languages
  - language auto-detection by suffix
  - `build_fqn` recipe (3-part path, extension strip, parent compose)
  - `code_symbol_node_id` determinism across calls
  - `parse_text` in-memory variant
  - empty / non-existent file → empty `ParseResult` (no raise)
  - `Symbol` / `CallSite` / `ParseResult` `extra="forbid"`

## See also

- Reference: [`chunker-symbol-aware`](chunker-symbol-aware.md)
- Reference: [`extractor-code-relationships`](extractor-code-relationships.md)
- [`docs/superpowers/plans/2026-05-25-symbol-aware-code-ingest.md`](../superpowers/plans/2026-05-25-symbol-aware-code-ingest.md) — SP-A through SP-E plan
