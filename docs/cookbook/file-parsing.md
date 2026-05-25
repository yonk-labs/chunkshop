# File parsing (PDF / Word / PowerPoint / Excel / HTML)

`FilesSource` dispatches every matched file to a per-extension parser. The
default `pip install chunkshop` pulls **no** parser libraries — pick the
extras you need.

## Built-in parsers

| Extension(s)              | Parser class | Backing library | Install extra |
|---------------------------|--------------|------------------|---------------|
| `.txt` `.md` `.markdown` `.rst` `.log` `.csv` `.tsv` (and unknown) | `TextParser` | stdlib | none |
| `.pdf`                    | `PDFParser`  | `pypdf`          | `chunkshop[pdf]` |
| `.docx`                   | `DOCXParser` | `python-docx`    | `chunkshop[docx]` |
| `.pptx`                   | `PPTXParser` | `python-pptx`    | `chunkshop[pptx]` |
| `.xlsx`                   | `XLSXParser` | `openpyxl`       | `chunkshop[xlsx]` |
| `.html` `.htm`            | `HTMLParser` | `beautifulsoup4` | `chunkshop[html]` |

Umbrella extras:

```bash
pip install 'chunkshop[office]'       # pdf + docx + pptx + xlsx
pip install 'chunkshop[all-parsers]'  # office + html
```

Extension lookup is case-insensitive and tolerant of a leading dot. Unknown
extensions fall back to `TextParser` (utf-8 with `errors="replace"`).

## Usage

```yaml
# config.yaml
source:
  type: files
  glob: /path/to/corpus/**/*       # any extension; dispatch by suffix
chunker:
  type: hierarchy
embedder:
  type: bge-small-en-v1.5-int8
sink:
  type: pgvector
  table: kb
  mode: overwrite
```

Run it:

```bash
chunkshop ingest --config config.yaml
```

Each emitted `Document` carries `metadata["parser"]` so downstream consumers
can tell which parser produced the text.

## Plug in a custom parser

Implement the `FileParser` protocol (one attribute, one method) and pass it
to `FilesSource` via the `parsers=` kwarg:

```python
from pathlib import Path
from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource


class UppercaseParser:
    supported_extensions = ["weird"]

    def parse(self, path: Path) -> str:
        return path.read_text().upper()


src = FilesSource(
    Cfg(type="files", glob="/tmp/data/*.weird"),
    parsers={"weird": UppercaseParser()},
)
for doc in src.iter_documents():
    ...
```

Custom parsers override the defaults for the extensions they declare. They
must follow the same shape:

- `supported_extensions: list[str]` — lowercase, no leading dot.
- `parse(self, path: Path) -> str` — return the extracted text. Raise
  `chunkshop.sources.parsers.ParserError` for recognized-but-broken files.

## Out of scope

- **OCR** for scanned PDFs/images. `PDFParser` returns whatever text pypdf
  can extract; scanned-image-only PDFs come back empty. Wire your own OCR
  step (tesseract, etc.) via a custom parser if you need it.
- **Format conversion** (`.doc` -> text, `.rtf`, etc.). The five formats
  above are the supported set; pre-convert legacy formats outside chunkshop.
- **EbookLib / EPUB**. Excluded on licensing grounds (AGPL). Use a custom
  parser if you need EPUB ingestion.

## Lean by default

`import chunkshop.sources.parsers` never touches `pypdf`, `python-docx`,
`python-pptx`, `openpyxl`, or `bs4`. Each backing library is imported the
first time its parser's `parse()` runs. A missing library raises a
`RuntimeError` with a `pip install chunkshop[<extra>]` hint, not an opaque
`ImportError`.
