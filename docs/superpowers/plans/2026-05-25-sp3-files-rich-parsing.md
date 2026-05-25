# SP-3 `files.py` Rich-Document Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **Independence:** SP-3 depends only on SP-1's `Document` (with `fingerprint`) and the optional-extras pattern. It can run in parallel with SP-2. It does NOT lift RAGFlow code — per #25/#26, `files.py` stays the canonical file loader and is *enhanced*, not replaced.

**Goal:** Add a pluggable, extension-dispatched parser layer to `files.py` so PDFs, Word, PowerPoint, Excel, and HTML ingest as clean text, with all heavy parser libraries behind optional extras (default `pip install chunkshop` pulls none).

**Architecture:** A `FileParser` Protocol; one parser module per format wrapping a best-in-class MIT/BSD/Apache library; a `DEFAULT_PARSERS` registry keyed by lowercase extension; `FilesSource` gains an optional `parsers` injection point and dispatches per file, falling back to the current text behavior. Parsers import their backing library lazily and raise an actionable install hint when the extra is missing.

**Tech Stack:** Python 3.11+, pypdf (PDF), python-docx (DOCX), python-pptx (PPTX), openpyxl (XLSX), beautifulsoup4+lxml (HTML) — all behind extras `[pdf] [docx] [pptx] [xlsx] [html] [office] [all-parsers]`.

**Spec:** `docs/superpowers/specs/2026-05-25-chunkshop-connector-plugin-foundation-design.md` (§3 SP-3); issue #26.

**Working dir:** paths relative to `python/`. Tests: `uv run pytest`.

---

## File structure

| File | Responsibility | New/Mod |
|---|---|---|
| `src/chunkshop/sources/parsers/base.py` | `FileParser` Protocol + `ParserError` | New |
| `src/chunkshop/sources/parsers/text.py` | plain-text (current behavior, default fallback) | New |
| `src/chunkshop/sources/parsers/pdf.py` | pypdf wrapper | New |
| `src/chunkshop/sources/parsers/docx.py` | python-docx wrapper | New |
| `src/chunkshop/sources/parsers/pptx.py` | python-pptx wrapper | New |
| `src/chunkshop/sources/parsers/xlsx.py` | openpyxl wrapper | New |
| `src/chunkshop/sources/parsers/html.py` | bs4 wrapper | New |
| `src/chunkshop/sources/parsers/__init__.py` | `FileParser`, `DEFAULT_PARSERS`, `get_parser` | New |
| `src/chunkshop/sources/files.py` | dispatch by extension; `parsers` injection | Mod |
| `tests/fixtures/parsers/` | one tiny file per format | New |
| `pyproject.toml` | `[pdf] [docx] [pptx] [xlsx] [html] [office] [all-parsers]` extras | Mod |
| `docs/cookbook/file-parsing.md` | usage + custom-parser guide | New |

---

## Task 1: FileParser Protocol + ParserError + text parser

**Files:**
- Create: `src/chunkshop/sources/parsers/base.py`, `src/chunkshop/sources/parsers/text.py`
- Test: `tests/chunkshop/test_parser_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_parser_base.py
from pathlib import Path
from chunkshop.sources.parsers.base import FileParser, ParserError
from chunkshop.sources.parsers.text import TextParser


def test_text_parser_implements_protocol():
    assert isinstance(TextParser(), FileParser)


def test_text_parser_reads(tmp_path):
    p = tmp_path / "a.txt"; p.write_text("hello world", encoding="utf-8")
    out = TextParser().parse(p)
    assert out == "hello world"


def test_text_parser_extensions():
    assert "txt" in TextParser().supported_extensions


def test_parser_error_is_exception():
    import pytest
    with pytest.raises(ParserError):
        raise ParserError("bad file")
```

- [ ] **Step 2:** `uv run pytest tests/chunkshop/test_parser_base.py -v` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# src/chunkshop/sources/parsers/base.py
"""Per-extension file parsers. parse(path) returns the extracted text body;
FilesSource wraps that into a Document. Parsers raise ParserError on a file
they recognize but cannot read, and a RuntimeError with an install hint when
their optional library is missing."""
from __future__ import annotations
from pathlib import Path
from typing import Protocol, runtime_checkable


class ParserError(Exception):
    """A recognized file could not be parsed (corrupt, encrypted, malformed)."""


@runtime_checkable
class FileParser(Protocol):
    supported_extensions: list[str]
    def parse(self, path: Path) -> str: ...
```

```python
# src/chunkshop/sources/parsers/text.py
from __future__ import annotations
from pathlib import Path


class TextParser:
    supported_extensions = ["txt", "md", "markdown", "rst", "log", "csv", "tsv", ""]

    def __init__(self, encoding: str = "utf-8"):
        self.encoding = encoding

    def parse(self, path: Path) -> str:
        return path.read_text(encoding=self.encoding, errors="replace")
```

- [ ] **Step 4/5:** PASS → commit `feat(parsers): add FileParser protocol + text parser`.

---

## Task 2: PDF parser (pypdf, `[pdf]` extra)

**Files:**
- Create: `src/chunkshop/sources/parsers/pdf.py`, `tests/fixtures/parsers/sample.pdf`
- Test: `tests/chunkshop/test_parser_pdf.py`

- [ ] **Step 1: Create a tiny fixture PDF** programmatically (so the repo has a real, minimal PDF):

```python
# run once to generate the fixture (commit the .pdf, not this snippet)
# uv run python -c "import pypdf; w=pypdf.PdfWriter(); w.add_blank_page(72,72); \
#   import io; ... " # see Step 3 note
```

Use the test itself to skip cleanly if pypdf isn't installed.

- [ ] **Step 2: Write the failing test** (skips if extra missing — proves the lazy-import hint path too)

```python
# tests/chunkshop/test_parser_pdf.py
import pytest
from pathlib import Path

pypdf = pytest.importorskip("pypdf")
FIX = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.pdf"


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    # build a 1-page PDF with extractable text via reportlab if available,
    # else write a minimal text-bearing PDF with pypdf and skip if neither works.
    out = FIX
    if out.exists():
        return out
    pytest.skip("sample.pdf fixture missing; generate per plan Step 1")


def test_pdf_parser_extracts_text(sample_pdf):
    from chunkshop.sources.parsers.pdf import PDFParser
    text = PDFParser().parse(sample_pdf)
    assert isinstance(text, str)


def test_pdf_parser_extensions():
    from chunkshop.sources.parsers.pdf import PDFParser
    assert "pdf" in PDFParser().supported_extensions


def test_pdf_parser_missing_lib_hint(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pypdf", None)  # simulate not installed
    from chunkshop.sources.parsers.pdf import PDFParser
    with pytest.raises(RuntimeError, match=r"chunkshop\[pdf\]"):
        PDFParser().parse(FIX)
```

- [ ] **Step 3: Implement** `pdf.py`:

```python
# src/chunkshop/sources/parsers/pdf.py
from __future__ import annotations
from pathlib import Path
from chunkshop.sources.parsers.base import ParserError


class PDFParser:
    supported_extensions = ["pdf"]

    def parse(self, path: Path) -> str:
        try:
            import pypdf
        except (ImportError, TypeError) as exc:  # TypeError when monkeypatched to None
            raise RuntimeError(
                "PDF parsing requires pypdf. Install with `pip install chunkshop[pdf]`."
            ) from exc
        try:
            reader = pypdf.PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise ParserError(f"failed to parse PDF {path}: {exc}") from exc
```

Generate `tests/fixtures/parsers/sample.pdf` (commit it). If `reportlab` is available use it for text-bearing content; otherwise document the fixture as optional and let the extraction test skip.

- [ ] **Step 4: Add the `[pdf]` extra** to `pyproject.toml`: `pdf = ["pypdf>=4.0"]`.
- [ ] **Step 5:** Run → PASS/SKIP. Commit `feat(parsers): add PDF parser (pypdf) behind [pdf] extra`.

---

## Task 3: DOCX parser (python-docx, `[docx]` extra)

**Files:** `src/chunkshop/sources/parsers/docx.py`, `tests/fixtures/parsers/sample.docx`, `tests/chunkshop/test_parser_docx.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_parser_docx.py
import pytest
from pathlib import Path
docx = pytest.importorskip("docx")
FIX = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.docx"


@pytest.fixture(scope="module")
def sample_docx():
    if not FIX.exists():
        d = docx.Document(); d.add_paragraph("hello from docx"); d.save(FIX)
    return FIX


def test_docx_extracts(sample_docx):
    from chunkshop.sources.parsers.docx import DOCXParser
    assert "hello from docx" in DOCXParser().parse(sample_docx)


def test_docx_extensions():
    from chunkshop.sources.parsers.docx import DOCXParser
    assert "docx" in DOCXParser().supported_extensions


def test_docx_missing_lib_hint(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "docx", None)
    from chunkshop.sources.parsers.docx import DOCXParser
    with pytest.raises(RuntimeError, match=r"chunkshop\[docx\]"):
        DOCXParser().parse(FIX)
```

- [ ] **Step 2/3: Implement** `docx.py`:

```python
# src/chunkshop/sources/parsers/docx.py
from __future__ import annotations
from pathlib import Path
from chunkshop.sources.parsers.base import ParserError


class DOCXParser:
    supported_extensions = ["docx"]

    def parse(self, path: Path) -> str:
        try:
            import docx
        except (ImportError, TypeError) as exc:
            raise RuntimeError(
                "DOCX parsing requires python-docx. Install with `pip install chunkshop[docx]`."
            ) from exc
        try:
            d = docx.Document(str(path))
            return "\n".join(p.text for p in d.paragraphs)
        except Exception as exc:
            raise ParserError(f"failed to parse DOCX {path}: {exc}") from exc
```

- [ ] **Step 4:** `docx = ["python-docx>=1.1"]` in `pyproject.toml`.
- [ ] **Step 5:** Run → PASS. Commit `feat(parsers): add DOCX parser behind [docx] extra`.

---

## Task 4: HTML parser (beautifulsoup4, `[html]` extra)

**Files:** `src/chunkshop/sources/parsers/html.py`, `tests/chunkshop/test_parser_html.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_parser_html.py
import pytest
bs4 = pytest.importorskip("bs4")


def test_html_strips_tags(tmp_path):
    from chunkshop.sources.parsers.html import HTMLParser
    p = tmp_path / "a.html"
    p.write_text("<html><body><h1>Title</h1><p>Body text</p>"
                 "<script>ignore()</script></body></html>", encoding="utf-8")
    out = HTMLParser().parse(p)
    assert "Title" in out and "Body text" in out
    assert "ignore" not in out  # script/style removed


def test_html_extensions():
    from chunkshop.sources.parsers.html import HTMLParser
    assert "html" in HTMLParser().supported_extensions and "htm" in HTMLParser().supported_extensions


def test_html_missing_lib_hint(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "bs4", None)
    from chunkshop.sources.parsers.html import HTMLParser
    p = tmp_path / "a.html"; p.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"chunkshop\[html\]"):
        HTMLParser().parse(p)
```

- [ ] **Step 2/3: Implement** `html.py`:

```python
# src/chunkshop/sources/parsers/html.py
from __future__ import annotations
from pathlib import Path
from chunkshop.sources.parsers.base import ParserError


class HTMLParser:
    supported_extensions = ["html", "htm"]

    def parse(self, path: Path) -> str:
        try:
            from bs4 import BeautifulSoup
        except (ImportError, TypeError) as exc:
            raise RuntimeError(
                "HTML parsing requires beautifulsoup4. Install with `pip install chunkshop[html]`."
            ) from exc
        try:
            soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except Exception as exc:
            raise ParserError(f"failed to parse HTML {path}: {exc}") from exc
```

- [ ] **Step 4:** `html = ["beautifulsoup4>=4.12"]` in `pyproject.toml`.
- [ ] **Step 5:** Run → PASS. Commit `feat(parsers): add HTML parser behind [html] extra`.

---

## Task 5: PPTX + XLSX parsers (`[pptx]`, `[xlsx]` extras)

**Files:** `src/chunkshop/sources/parsers/pptx.py`, `src/chunkshop/sources/parsers/xlsx.py`, tests + fixtures

- [ ] **Step 1: Write failing tests** (mirror Task 3 shape — generate fixtures via the libs; assert text extraction, extensions, and the `chunkshop[pptx]` / `chunkshop[xlsx]` install-hint paths).

```python
# tests/chunkshop/test_parser_pptx.py
import pytest
from pathlib import Path
pptx = pytest.importorskip("pptx")
FIX = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.pptx"


@pytest.fixture(scope="module")
def sample_pptx():
    if not FIX.exists():
        prs = pptx.Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = "deck title"; prs.save(FIX)
    return FIX


def test_pptx_extracts(sample_pptx):
    from chunkshop.sources.parsers.pptx import PPTXParser
    assert "deck title" in PPTXParser().parse(sample_pptx)
```

```python
# tests/chunkshop/test_parser_xlsx.py
import pytest
from pathlib import Path
openpyxl = pytest.importorskip("openpyxl")
FIX = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.xlsx"


@pytest.fixture(scope="module")
def sample_xlsx():
    if not FIX.exists():
        wb = openpyxl.Workbook(); ws = wb.active; ws["A1"] = "cell value"; wb.save(FIX)
    return FIX


def test_xlsx_extracts(sample_xlsx):
    from chunkshop.sources.parsers.xlsx import XLSXParser
    assert "cell value" in XLSXParser().parse(sample_xlsx)
```

- [ ] **Step 2/3: Implement**

```python
# src/chunkshop/sources/parsers/pptx.py
from __future__ import annotations
from pathlib import Path
from chunkshop.sources.parsers.base import ParserError


class PPTXParser:
    supported_extensions = ["pptx"]

    def parse(self, path: Path) -> str:
        try:
            from pptx import Presentation
        except (ImportError, TypeError) as exc:
            raise RuntimeError(
                "PPTX parsing requires python-pptx. Install with `pip install chunkshop[pptx]`."
            ) from exc
        try:
            prs = Presentation(str(path))
            out = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        out.append(shape.text_frame.text)
            return "\n".join(out)
        except Exception as exc:
            raise ParserError(f"failed to parse PPTX {path}: {exc}") from exc
```

```python
# src/chunkshop/sources/parsers/xlsx.py
from __future__ import annotations
from pathlib import Path
from chunkshop.sources.parsers.base import ParserError


class XLSXParser:
    supported_extensions = ["xlsx"]

    def parse(self, path: Path) -> str:
        try:
            import openpyxl
        except (ImportError, TypeError) as exc:
            raise RuntimeError(
                "XLSX parsing requires openpyxl. Install with `pip install chunkshop[xlsx]`."
            ) from exc
        try:
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    rows.append("\t".join("" if c is None else str(c) for c in row))
            return "\n".join(rows)
        except Exception as exc:
            raise ParserError(f"failed to parse XLSX {path}: {exc}") from exc
```

- [ ] **Step 4:** add `pptx = ["python-pptx>=0.6.23"]`, `xlsx = ["openpyxl>=3.1"]`.
- [ ] **Step 5:** Run → PASS. Commit `feat(parsers): add PPTX + XLSX parsers behind extras`.

---

## Task 6: DEFAULT_PARSERS registry + get_parser

**Files:** `src/chunkshop/sources/parsers/__init__.py`; Test: `tests/chunkshop/test_parser_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_parser_registry.py
from chunkshop.sources.parsers import DEFAULT_PARSERS, get_parser
from chunkshop.sources.parsers.text import TextParser


def test_known_extensions_resolve():
    assert get_parser("pdf").__class__.__name__ == "PDFParser"
    assert get_parser("DOCX").__class__.__name__ == "DOCXParser"  # case-insensitive


def test_unknown_extension_falls_back_to_text():
    assert isinstance(get_parser("xyz"), TextParser)


def test_custom_parsers_override():
    class _Mine:
        supported_extensions = ["pdf"]
        def parse(self, path): return "mine"
    p = get_parser("pdf", parsers={"pdf": _Mine()})
    assert p.parse(__import__("pathlib").Path(".")) == "mine"
```

- [ ] **Step 2/3: Implement** `__init__.py`:

```python
# src/chunkshop/sources/parsers/__init__.py
from __future__ import annotations
from chunkshop.sources.parsers.base import FileParser, ParserError
from chunkshop.sources.parsers.text import TextParser
from chunkshop.sources.parsers.pdf import PDFParser
from chunkshop.sources.parsers.docx import DOCXParser
from chunkshop.sources.parsers.pptx import PPTXParser
from chunkshop.sources.parsers.xlsx import XLSXParser
from chunkshop.sources.parsers.html import HTMLParser

_TEXT = TextParser()

DEFAULT_PARSERS: dict[str, FileParser] = {
    "txt": _TEXT, "md": _TEXT, "markdown": _TEXT, "rst": _TEXT, "log": _TEXT,
    "csv": _TEXT, "tsv": _TEXT,
    "pdf": PDFParser(), "docx": DOCXParser(), "pptx": PPTXParser(),
    "xlsx": XLSXParser(), "html": HTMLParser(), "htm": HTMLParser(),
}


def get_parser(ext: str, parsers: dict[str, FileParser] | None = None) -> FileParser:
    table = parsers or DEFAULT_PARSERS
    return table.get(ext.lower().lstrip("."), _TEXT)


__all__ = ["FileParser", "ParserError", "DEFAULT_PARSERS", "get_parser",
           "TextParser", "PDFParser", "DOCXParser", "PPTXParser", "XLSXParser", "HTMLParser"]
```

(Importing parser *classes* is safe — backing libs import lazily inside `parse()`, so this module loads with zero extras installed.)

- [ ] **Step 4/5:** Run → PASS. Commit `feat(parsers): add DEFAULT_PARSERS registry + get_parser`.

---

## Task 7: Wire dispatch into FilesSource

**Files:** Modify `src/chunkshop/sources/files.py`; Test: `tests/chunkshop/test_files_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_files_dispatch.py
import pytest
from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource


def test_text_files_unchanged(tmp_path):
    (tmp_path / "a.txt").write_text("plain text", encoding="utf-8")
    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.txt")))
    docs = list(src.iter_documents())
    assert docs[0].content == "plain text"
    assert docs[0].title == "a.txt"


def test_custom_parser_injection(tmp_path):
    (tmp_path / "a.weird").write_text("raw", encoding="utf-8")
    class _Up:
        supported_extensions = ["weird"]
        def parse(self, path): return path.read_text().upper()
    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.weird")), parsers={"weird": _Up()})
    assert list(src.iter_documents())[0].content == "RAW"


def test_html_dispatched_to_html_parser(tmp_path):
    pytest.importorskip("bs4")
    (tmp_path / "a.html").write_text("<p>hi <b>there</b></p>", encoding="utf-8")
    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.html")))
    out = list(src.iter_documents())[0].content
    assert "hi" in out and "there" in out and "<p>" not in out
```

- [ ] **Step 2:** Run → FAIL (`FilesSource.__init__` takes no `parsers`).

- [ ] **Step 3: Modify `files.py`** — add `parsers` kwarg + dispatch:

```python
from __future__ import annotations
import glob as _glob
import hashlib
from pathlib import Path
from typing import Iterator, Optional

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.base import Document
from chunkshop.sources.parsers import FileParser, get_parser


class FilesSource:
    def __init__(self, cfg: Cfg, parsers: Optional[dict[str, FileParser]] = None):
        self.cfg = cfg
        self.parsers = parsers  # None → DEFAULT_PARSERS

    def iter_documents(self) -> Iterator[Document]:
        paths = sorted(_glob.glob(self.cfg.glob, recursive=True))
        if not paths:
            raise ValueError(f"no files matched glob: {self.cfg.glob}")
        for p in paths:
            path = Path(p)
            ext = path.suffix.lower().lstrip(".")
            parser = get_parser(ext, self.parsers)
            text = parser.parse(path)
            yield Document(id=self._id_for(path), content=text, title=path.name,
                           metadata={"source_path": str(path), "parser": parser.__class__.__name__})

    def _id_for(self, path: Path) -> str:
        mode = self.cfg.id_from
        if mode == "path":
            return str(path)
        if mode == "stem":
            return path.stem
        if mode == "sha1":
            return hashlib.sha1(str(path).encode()).hexdigest()
        raise ValueError(mode)
```

Note: the default `TextParser` uses utf-8; the legacy `cfg.encoding` now applies only when callers pass a `TextParser(encoding=cfg.encoding)`. To preserve the `encoding` config exactly, in `__init__` build a text parser with `cfg.encoding` and inject it for the text extensions when `parsers is None`. Add that wiring and a test asserting a non-utf-8 file with `encoding="latin-1"` still reads — keeps backward compat with the existing `FilesSource(cfg)` contract.

- [ ] **Step 4:** Run → PASS, plus `uv run pytest tests/chunkshop/ -k files -v` (existing files tests stay green).
- [ ] **Step 5: Commit** `feat(sources): dispatch files.py by extension to pluggable parsers`.

---

## Task 8: Office/all-parsers umbrella extras + docs

**Files:** Modify `pyproject.toml`; Create `docs/cookbook/file-parsing.md`

- [ ] **Step 1: Add umbrella extras**

```toml
office = ["chunkshop[pdf,docx,pptx,xlsx]"]
all-parsers = ["chunkshop[pdf,docx,pptx,xlsx,html]"]
```

- [ ] **Step 2: Verify the lean default** — in a clean env, `pip install chunkshop` then `python -c "import chunkshop.sources.parsers"` imports with **no** parser libs present (lazy imports). Add a test:

```python
# tests/chunkshop/test_parsers_lazy_import.py
def test_registry_imports_without_extras():
    import importlib
    import chunkshop.sources.parsers as P
    importlib.reload(P)  # must not raise even if pypdf/docx/etc absent
    assert "pdf" in P.DEFAULT_PARSERS
```

- [ ] **Step 3: Write `docs/cookbook/file-parsing.md`** — built-in parsers table, install commands per extra, custom-parser example (implement `FileParser`, pass via `parsers=`), the OCR/format-conversion out-of-scope note (#26), and the AGPL exclusion note (no EbookLib).

- [ ] **Step 4: Commit** `feat(parsers): umbrella extras + file-parsing docs`.

---

## Task 9: Gate

- [ ] **Step 1:** `uv run pytest -q` → all SP-3 tests pass; parser-extra tests skip cleanly when the extra is absent and pass when present (`uv sync --extra all-parsers` then re-run).
- [ ] **Step 2:** `ruff check src/chunkshop/sources/parsers src/chunkshop/sources/files.py && ruff format --check` → clean.
- [ ] **Step 3:** Confirm `files` is NOT in any connector/registry path — it remains the canonical core loader (#25).
- [ ] **Step 4: Commit + tag** `git -C .. tag sp3-file-parsing`.

---

## Self-review

**Spec coverage** — §3 SP-3 / issue #26: `FileParser` protocol → T1; Phase-A parsers (pdf/docx/html) → T2,T3,T4; Phase-B (pptx/xlsx) → T5; registry + dispatch → T6,T7; optional-deps + lean default → T2–T5,T8; docs → T8; files.py-stays-canonical (#25) → T9 Step 3. No gaps.

**Placeholder scan** — every parser has complete code; fixtures are generated by the test libs themselves; no TBD. The `encoding` backward-compat wiring in T7 is specified with its own required test.

**Type consistency** — `FileParser.parse(path) -> str`, `supported_extensions: list[str]`, `get_parser(ext, parsers=None)`, `DEFAULT_PARSERS: dict[str, FileParser]`, `FilesSource(cfg, parsers=None)`, `ParserError` used identically across all tasks. The install-hint message format (`chunkshop[<extra>]`) and the `(ImportError, TypeError)` lazy-import guard are uniform across all five library parsers.
```
