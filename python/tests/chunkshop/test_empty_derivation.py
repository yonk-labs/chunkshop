from pathlib import Path

import pytest

from chunkshop.config import FilesSource as Cfg
from chunkshop.config import ImageMetaDeriverConfig
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


def test_text_only_error_message_has_no_media_words(tmp_path):
    """NN-6: a text-only user must never see a media concept in an error."""
    with pytest.raises(EmptyDerivationError) as exc_info:
        list(_src(tmp_path).iter_documents())
    message = str(exc_info.value).lower()
    for banned in ("ocr", "image", "scanned", "pdf"):
        assert banned not in message, f"{banned!r} leaked into a media-free error: {message!r}"
    # Still actionable: names all three on_empty modes.
    assert "skip" in message
    assert "index" in message
    assert "error" in message or "on_empty" in message


def test_derive_configured_error_message_offers_ocr_hint(tmp_path):
    """A cell with a derive block gets the media-specific OCR hint."""
    (tmp_path / "a.blank").write_text("x", encoding="utf-8")
    src = FilesSource(
        Cfg(
            type="files",
            glob=str(tmp_path / "*.blank"),
            derive={"png": ImageMetaDeriverConfig(rung="image_meta")},
        ),
        parsers={"blank": _EmptyDeriver()},
    )
    with pytest.raises(EmptyDerivationError) as exc_info:
        list(src.iter_documents())
    assert "ocr" in str(exc_info.value).lower()


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
