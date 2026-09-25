# File parsers — pluggable per-extension dispatch (SP-3)

**Module**: `chunkshop.sources.parsers`
**Type**: Utility — file-parser layer used by `FilesSource`
**Ship status**: verified
**Optional extras**: per-parser (see table below)
**Since**: 2026-05-25 (SP-3 track — multiple commits)

## Purpose

Convert files on disk into the text body that `FilesSource` hands to the
runner. Each extension routes through a `FileParser` (a Protocol):
text-family extensions go to the lightweight `TextParser`; rich
formats (PDF, DOCX, PPTX, XLSX, HTML) dispatch to format-specific
parsers behind optional extras.

The layer is pluggable — `FilesSource(cfg, parsers={"weird": _MyParser()})`
overrides per-extension. Importing the parsers module is safe with no
extras installed; backing libraries import lazily inside each parser's
`parse()`.

## Parsers-by-extension table

| Extension(s)                  | Parser class    | Extra needed              | Backing lib   | Notes |
|-------------------------------|-----------------|---------------------------|---------------|-------|
| `txt`, `md`, `markdown`, `rst`, `log`, `csv`, `tsv`, `""` | `TextParser` | (none) | stdlib `pathlib` | Honors `cfg.encoding`. |
| `pdf`                         | `PDFParser`     | `chunkshop[pdf]`          | `pypdf`       | Per-page text extraction; ParserError on corrupt/encrypted. |
| `docx`                        | `DOCXParser`    | `chunkshop[docx]`         | `python-docx` | Paragraphs only — no tables/headers/footers. |
| `pptx`                        | `PPTXParser`    | `chunkshop[pptx]`         | `python-pptx` | Text frames from all shapes on all slides. |
| `xlsx`                        | `XLSXParser`    | `chunkshop[xlsx]`         | `openpyxl`    | Read-only, data_only mode. Cells joined by tab; rows by newline. |
| `html`, `htm`                 | `HTMLParser`    | `chunkshop[html]`         | `beautifulsoup4` | Strips `<script>`/`<style>`; `get_text(separator="\n", strip=True)`. |
| anything else                 | `TextParser` (fallback) | (none)            | stdlib        | `get_parser()` returns TextParser for unknown extensions. |

## Umbrella extras

| Extra                | Pulls in                       |
|----------------------|--------------------------------|
| `chunkshop[office]`  | `[pdf, docx, pptx, xlsx]`      |
| `chunkshop[all-parsers]` | `[pdf, docx, pptx, xlsx, html]` |

## Public API

```python
from chunkshop.sources.parsers import (
    FileParser,
    Deriver,                # generalisation of FileParser (media ingest)
    ParseResult,
    ParserError,
    EmptyDerivationError,
    AmbiguousDeriverError,
    DEFAULT_PARSERS,
    get_parser,
    call_deriver,
    cache_key_extra_for,
    normalize_parse_result,
    TextParser,
    PDFParser,
    DOCXParser,
    PPTXParser,
    XLSXParser,
    HTMLParser,
)

class ParserError(Exception):
    """A recognized file could not be parsed (corrupt, encrypted, malformed)."""

class FileParser(Protocol):
    supported_extensions: list[str]
    def parse(self, path: Path) -> str: ...

# The generalisation. Everything on it is optional for backward
# compatibility; FilesSource probes with getattr.
class Deriver(Protocol):
    supported_extensions: list[str]
    def derive(self, path: Path) -> str | ParseResult: ...
    def cache_key_extra(self, path: Path) -> str | None: ...  # default: None

@dataclass(frozen=True)
class ParseResult:
    text: str
    metadata: dict = field(default_factory=dict)

def call_deriver(obj, path: Path) -> ParseResult: ...
def cache_key_extra_for(obj, path: Path) -> str | None: ...

DEFAULT_PARSERS: dict[str, FileParser]

def get_parser(
    ext: str,
    parsers: dict[str, FileParser] | None = None,
) -> FileParser: ...
```

## Behavior contract

1. **Lazy import.** Backing libs (`pypdf`, `python-docx`,
   `python-pptx`, `openpyxl`, `bs4`) import inside `parse()`, not at
   module load.
2. **Missing extra → `RuntimeError`** with a clear install hint:
   ```
   PDF parsing requires pypdf. Install with `pip install chunkshop[pdf]`.
   ```
3. **Parser-level failure → `ParserError`** with the path + cause.
   Caller can catch and skip the file.
4. **`get_parser(ext, parsers=None)`** is case-insensitive and tolerates
   a leading dot. `"PDF"`, `"pdf"`, `".pdf"` all match.
5. **Unknown extension** falls through to a `TextParser` — better to
   try than to crash.
6. **Custom parsers** passed to `FilesSource(parsers=...)` override the
   defaults.
7. **`DEFAULT_PARSERS` is a module-level singleton.** Reusing the same
   parser instances across `FilesSource` instances is intentional
   (parsers are stateless).
8. **`TextParser` honors `cfg.encoding`.** When `FilesSource` builds
   its effective parser table without an explicit `parsers=` override,
   it overlays a `TextParser(encoding=cfg.encoding)` onto the text-family
   defaults — back-compat for legacy `cfg.encoding` config.
9. **`derive()` wins over `parse()`, and a bare `str` is auto-wrapped.**
   `call_deriver` prefers `.derive()` when an object has both, and
   normalises either a `str` or a `ParseResult` into a `ParseResult` —
   so a third-party parser written against the `parse(path) -> str`
   signature keeps working unchanged.
10. **Empty text from a *deriver* is a failure by default; empty text
    from a parser is not.** A deriver returning `""` raises
    `EmptyDerivationError` unless `source.on_empty` is `skip` or
    `index`. The plain-parser path is unaffected by that setting — a
    zero-byte or whitespace-only file handled by a `FileParser` is
    indexed as an empty document, as it always was. `on_empty` is a
    derivation policy, not a parser policy.

## The `derive:` layer

`source.derive` layers **derivers** over this parser table, keyed by a
pipe-separated extension list, and is how non-text bytes (images, and
audio/video via rung 5) become text. A deriver occupies the same slot a
`FileParser` does; it just also returns metadata and provenance, and
participates in a provenance-keyed derivation cache.

Three rungs ship — see [`deriver-image-meta`](deriver-image-meta.md),
[`deriver-clip-tags`](deriver-clip-tags.md),
[`deriver-ross`](deriver-ross.md) and the guide at
[`docs/media-ingest.md`](../media-ingest.md). Only extensions named in a
`derive:` block are cache-routed; the parsers above never are.

## Inputs

- `Path` to a file on disk.

## Outputs

- The parsed text body as a `str`, or a `ParseResult(text, metadata)`
  from a deriver. PDF / PPTX / XLSX use newlines between pages / slides
  / rows.

## Errors

| Exception     | When |
|---------------|------|
| `RuntimeError` | Backing library missing — install the right `chunkshop[X]` extra. |
| `ParserError`  | File recognized but failed to parse (corrupt, encrypted, malformed). |
| `EmptyDerivationError` | A **derive-routed** file was read but produced no text, and `source.on_empty` is `error` (the default). Not raised on the plain-parser path. Subclass of `ParserError`. |
| `AmbiguousDeriverError` | Two different `derive:` keys route the same extension. Rejected outright rather than letting insertion order decide, because Python and Rust would pick opposite winners for identical YAML. Subclass of `ParserError`. |

## Example: zero-config

```yaml
source:
  type: files
  glob: "/path/to/corpus/**/*"  # mixed PDF/DOCX/MD/HTML
  id_from: path
  # parsers auto-dispatch based on extension
```

With this, install the union of extras you need:

```bash
pip install "chunkshop[office,html]"
# or for everything:
pip install "chunkshop[all-parsers]"
```

## Example: custom parser injection

```python
from pathlib import Path
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers import FileParser
from chunkshop.config import FilesSource as Cfg

class WeirdParser:
    supported_extensions = ["weird"]
    def parse(self, path: Path) -> str:
        # ... your custom logic
        return path.read_text().upper()

src = FilesSource(
    Cfg(type="files", glob="/data/**/*", id_from="path"),
    parsers={"weird": WeirdParser()},
)
```

The custom map only overrides extensions you specify — everything else
still uses defaults.

## Example: opt-out of default rich parsers

```python
from chunkshop.sources.parsers import TextParser
src = FilesSource(
    cfg,
    parsers={"txt": TextParser(), "md": TextParser()},
)
# Now PDF / DOCX / etc. fall through to TextParser too — they'll
# decode as text (probably garbage), but they won't require any extras.
```

## How it integrates with the pipeline

`FilesSource.iter_documents` calls `get_parser(ext, self._parsers)` per
file, then `call_deriver(parser, path)`. The returned text becomes
`Document.content` and any returned metadata is merged into
`Document.metadata` with **source keys winning**.
`Document.metadata["parser"]` records which parser or deriver class ran
(`"PDFParser"`, `"TextParser"`, `"ClipTagsDeriver"`, …) — useful for
filtering in downstream queries — and is stamped by `FilesSource` over
anything a deriver emitted under that name.

## Tests proving the contract

- `tests/chunkshop/test_parsers_*` (one file per parser):
  - lazy-import contract (each parser raises RuntimeError when its lib
    is monkeypatched to None)
  - happy-path text extraction
  - ParserError on a corrupt fixture
  - `DEFAULT_PARSERS` keys
  - `get_parser` case insensitivity
  - `FilesSource` integration with custom parsers
- Cookbook: [`docs/cookbook/file-parsing.md`](../cookbook/file-parsing.md).

## See also

- [`docs/media-ingest.md`](../media-ingest.md) — the `derive:` layer:
  rungs, provenance, the derivation cache
- Reference: [`deriver-image-meta`](deriver-image-meta.md),
  [`deriver-clip-tags`](deriver-clip-tags.md),
  [`deriver-ross`](deriver-ross.md) — the three shipped rungs
- [`docs/cookbook/file-parsing.md`](../cookbook/file-parsing.md) —
  the SP-3 cookbook (install matrix, behavior of each parser)
- Reference: source-blob, source-gdrive — connector sources whose
  emitted text comes pre-parsed (Drive Google-Docs exported as text,
  S3 raw bodies)
