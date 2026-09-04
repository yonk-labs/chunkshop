"""Spec defect F5 — "empty-derivation is loud, not silent".

A parser that extracts no text from a file which DEMONSTRABLY HAS content
must fail loudly rather than index a silent empty document. The canonical
case is a scanned PDF: pages, no text layer, zero characters.

The other half of this file is the regression guard for the opposite bug.
`on_empty` firing on the plain-text path aborted a whole cell on a
zero-byte `.md` — after `mode: overwrite` had already dropped the table.
The two cases must stay separated: a blank text file is LEGITIMATELY empty
(bytes in, no text out, nothing lost) and is indexed, while a PDF with
pages and no characters is a failed extraction and is raised. The line
between them is an affirmative `ParseResult.had_content` claim from the
parser, never "the text came out blank" — see `test_the_trigger_is_the_
parser_claim_not_the_empty_text` below, which nails that down directly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers import EmptyDerivationError, ParseResult

FIX = Path(__file__).parents[1] / "fixtures" / "parsers"


def _copy(fixture: str, tmp_path: Path, name: str | None = None) -> Path:
    src = FIX / fixture
    if not src.exists():  # pragma: no cover - fixture is committed
        pytest.skip(f"fixture {fixture} missing")
    dst = tmp_path / (name or fixture)
    shutil.copy(src, dst)
    return dst


def _src(tmp_path: Path, pattern: str, **kw) -> FilesSource:
    return FilesSource(Cfg(type="files", glob=str(tmp_path / pattern), **kw))


# --- F5: loud by default ---------------------------------------------------


def test_scanned_pdf_fails_loudly_by_default(tmp_path):
    """The named defect. `parsers/pdf.py`'s `or ""` used to swallow this."""
    pytest.importorskip("pypdf")
    _copy("scanned.pdf", tmp_path)
    with pytest.raises(EmptyDerivationError) as exc:
        list(_src(tmp_path, "*.pdf").iter_documents())
    msg = str(exc.value)
    # Actionable: names the file, says what it HAD, and points at the real
    # remedy (preprocessing) before the escape hatch.
    assert "scanned.pdf" in msg
    assert "1 page(s)" in msg
    assert "extracted no text" in msg
    assert "no text layer" in msg
    assert "does not OCR" in msg
    assert "yonk-doctools" in msg
    assert "source.on_empty='skip'" in msg


def test_docx_with_only_a_table_fails_loudly(tmp_path):
    pytest.importorskip("docx")
    _copy("table_only.docx", tmp_path)
    with pytest.raises(EmptyDerivationError, match=r"1 table\(s\)"):
        list(_src(tmp_path, "*.docx").iter_documents())


def test_pptx_with_no_slide_text_fails_loudly(tmp_path):
    pytest.importorskip("pptx")
    _copy("image_only.pptx", tmp_path)
    with pytest.raises(EmptyDerivationError, match=r"1 slide\(s\)"):
        list(_src(tmp_path, "*.pptx").iter_documents())


def test_image_only_html_fails_loudly(tmp_path):
    pytest.importorskip("bs4")
    (tmp_path / "a.html").write_text(
        "<html><body><img src='scan-01.png'><img src='scan-02.png'>"
        "<svg viewBox='0 0 1 1'></svg></body></html>",
        encoding="utf-8",
    )
    with pytest.raises(EmptyDerivationError) as exc:
        list(_src(tmp_path, "*.html").iter_documents())
    assert "3 embedded element(s) (img, svg)" in str(exc.value)


# --- the escape hatches ----------------------------------------------------


def test_on_empty_skip_omits_the_file_and_continues(tmp_path):
    pytest.importorskip("pypdf")
    _copy("scanned.pdf", tmp_path)
    _copy("sample.pdf", tmp_path)
    docs = list(_src(tmp_path, "*.pdf", on_empty="skip").iter_documents())
    assert [d.title for d in docs] == ["sample.pdf"]


def test_on_empty_index_restores_the_silent_empty_document(tmp_path):
    pytest.importorskip("pypdf")
    _copy("scanned.pdf", tmp_path)
    docs = list(_src(tmp_path, "*.pdf", on_empty="index").iter_documents())
    assert len(docs) == 1
    assert docs[0].content.strip() == ""


# --- unaffected: text-bearing files ----------------------------------------


def test_text_bearing_pdf_is_unaffected(tmp_path):
    pytest.importorskip("pypdf")
    _copy("sample.pdf", tmp_path)
    docs = list(_src(tmp_path, "*.pdf").iter_documents())
    assert len(docs) == 1
    assert "hello from pdf" in docs[0].content
    assert docs[0].metadata["parser"] == "PDFParser"


def test_text_bearing_docx_and_pptx_are_unaffected(tmp_path):
    pytest.importorskip("docx")
    pytest.importorskip("pptx")
    _copy("sample.docx", tmp_path)
    _copy("sample.pptx", tmp_path)
    docs = list(_src(tmp_path, "sample.*").iter_documents())
    assert len(docs) == 2
    assert all(d.content.strip() for d in docs)


# --- REGRESSION GUARD: blank text files must stay indexable ----------------
#
# These are the tests that fail if someone re-widens the check from "the
# parser claimed unextracted content" back to "the extracted text is blank".


@pytest.mark.parametrize("mode", ["error", "skip", "index"])
def test_blank_text_files_index_under_every_on_empty_mode(tmp_path, mode):
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    (tmp_path / "whitespace.md").write_text("   \n\t\n  ", encoding="utf-8")
    (tmp_path / "real.md").write_text("# Real\n\nBody.\n", encoding="utf-8")
    docs = list(_src(tmp_path, "*.md", on_empty=mode).iter_documents())
    # `skip` is the one mode that legitimately drops a document — but only a
    # derive-routed or parser-flagged one, never a plain empty text file.
    assert len(docs) == 3, f"on_empty={mode} dropped a plain text file"
    assert sorted(d.title for d in docs) == [
        "empty.md",
        "real.md",
        "whitespace.md",
    ]
    assert {d.content for d in docs} == {"", "   \n\t\n  ", "# Real\n\nBody.\n"}


def test_zero_byte_file_of_every_text_extension_indexes(tmp_path):
    exts = ["txt", "md", "markdown", "rst", "log", "csv", "tsv"]
    for e in exts:
        (tmp_path / f"blank.{e}").write_text("", encoding="utf-8")
    docs = list(_src(tmp_path, "blank.*").iter_documents())
    assert len(docs) == len(exts)


def test_blank_workbook_is_not_a_failure(tmp_path):
    """XLSX deliberately makes no F5 claim — see `XLSXParser.parse`.

    A workbook of empty cells is a blank template, not a failed extraction,
    and neither port's library exposes non-cell content publicly. Raising
    here would be a false positive, so it indexes.
    """
    pytest.importorskip("openpyxl")
    _copy("blank.xlsx", tmp_path)
    docs = list(_src(tmp_path, "*.xlsx").iter_documents())
    assert len(docs) == 1
    assert docs[0].content.strip() == ""


def test_empty_html_with_no_embedded_content_is_not_a_failure(tmp_path):
    pytest.importorskip("bs4")
    (tmp_path / "a.html").write_text(
        "<html><body></body></html>", encoding="utf-8"
    )
    docs = list(_src(tmp_path, "*.html").iter_documents())
    assert len(docs) == 1 and docs[0].content.strip() == ""


# --- the mechanism itself --------------------------------------------------


def test_the_trigger_is_the_parser_claim_not_the_empty_text(tmp_path):
    """Two parsers, identical empty output, opposite outcomes.

    This is the whole scoping rule in one test. If someone changes the
    condition in `files.py` from `result.had_content` to `not
    result.text.strip()`, the `quiet` half of this test starts raising and
    yesterday's data-loss regression is back.
    """
    (tmp_path / "a.quiet").write_text("x", encoding="utf-8")
    (tmp_path / "a.loud").write_text("x", encoding="utf-8")

    class _Quiet:
        supported_extensions = ["quiet"]

        def parse(self, path):
            return "   "

    class _Loud:
        supported_extensions = ["loud"]

        def parse(self, path):
            return ParseResult(text="   ", had_content="7 widget(s)")

    quiet = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.quiet")),
        parsers={"quiet": _Quiet()},
    )
    assert len(list(quiet.iter_documents())) == 1

    loud = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.loud")),
        parsers={"loud": _Loud()},
    )
    with pytest.raises(EmptyDerivationError, match=r"7 widget\(s\)"):
        list(loud.iter_documents())


def test_legacy_str_returning_parser_makes_no_claim():
    """A third-party parser predating `had_content` is treated as silent."""
    from chunkshop.sources.parsers import normalize_parse_result

    assert normalize_parse_result("").had_content is None
    assert normalize_parse_result(ParseResult(text="")).had_content is None
