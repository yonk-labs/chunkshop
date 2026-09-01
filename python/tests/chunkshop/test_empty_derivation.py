from pathlib import Path

import pytest

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers.base import EmptyDerivationError


class _EmptyDeriver:
    supported_extensions = ["blank"]

    def derive(self, path: Path) -> str:
        return "   \n  "  # whitespace only — as good as empty


def _src(tmp_path, **kw):
    (tmp_path / "a.blank").write_text("x", encoding="utf-8")
    return FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.blank"), **kw),
        parsers={"blank": _EmptyDeriver()},
    )


def test_empty_derivation_raises_by_default(tmp_path):
    with pytest.raises(EmptyDerivationError, match="a.blank"):
        list(_src(tmp_path).iter_documents())


def test_on_empty_skip_omits_the_document(tmp_path):
    assert list(_src(tmp_path, on_empty="skip").iter_documents()) == []


def test_on_empty_index_preserves_legacy_behaviour(tmp_path):
    docs = list(_src(tmp_path, on_empty="index").iter_documents())
    assert len(docs) == 1
    assert docs[0].content.strip() == ""


def test_pdf_with_no_extractable_text_raises(tmp_path):
    """The actual shipping defect: a scanned PDF silently indexed as empty."""
    pytest.importorskip("pypdf")
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    target = tmp_path / "scanned.pdf"
    with target.open("wb") as fh:
        writer.write(fh)

    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.pdf")))
    with pytest.raises(EmptyDerivationError, match="scanned.pdf"):
        list(src.iter_documents())


def test_on_empty_skip_also_applies_to_the_incremental_path(tmp_path):
    """iter_changes_since must not yield None for a skipped document."""
    (tmp_path / "a.blank").write_text("x", encoding="utf-8")
    src = FilesSource(
        Cfg(
            type="files",
            glob=str(tmp_path / "*.blank"),
            on_empty="skip",
            incremental={"cursor_path": str(tmp_path / "cursor.json")},
        ),
        parsers={"blank": _EmptyDeriver()},
    )
    docs = list(src.iter_changes_since(src.empty_cursor()))
    assert docs == [], "no None may leak into the runner"
