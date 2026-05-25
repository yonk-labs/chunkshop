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

        def parse(self, path):
            return path.read_text().upper()

    src = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.weird")),
        parsers={"weird": _Up()},
    )
    assert list(src.iter_documents())[0].content == "RAW"


def test_html_dispatched_to_html_parser(tmp_path):
    pytest.importorskip("bs4")
    (tmp_path / "a.html").write_text(
        "<p>hi <b>there</b></p>", encoding="utf-8"
    )
    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.html")))
    out = list(src.iter_documents())[0].content
    assert "hi" in out and "there" in out and "<p>" not in out


def test_pdf_dispatched_to_pdf_parser(tmp_path):
    pytest.importorskip("pypdf")
    # Use the pre-built sample fixture rather than recreating it inline.
    from pathlib import Path
    import shutil

    src_fix = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.pdf"
    if not src_fix.exists():
        pytest.skip("sample.pdf fixture missing")
    dst = tmp_path / "a.pdf"
    shutil.copy(src_fix, dst)
    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.pdf")))
    docs = list(src.iter_documents())
    assert docs[0].metadata["parser"] == "PDFParser"
    assert isinstance(docs[0].content, str)


def test_metadata_includes_parser_name(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.txt")))
    doc = list(src.iter_documents())[0]
    assert doc.metadata["parser"] == "TextParser"
    assert doc.metadata["source_path"].endswith("a.txt")


def test_legacy_encoding_backward_compat(tmp_path):
    # Backward-compat: cfg.encoding="latin-1" must still drive text reads when
    # no custom parsers are injected.
    raw = "café".encode("latin-1")
    (tmp_path / "a.txt").write_bytes(raw)
    src = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.txt"), encoding="latin-1")
    )
    doc = list(src.iter_documents())[0]
    assert doc.content == "café"


def test_no_files_matched_raises(tmp_path):
    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.nope")))
    with pytest.raises(ValueError, match="no files matched glob"):
        list(src.iter_documents())
