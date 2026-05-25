import pytest
from pathlib import Path

docx = pytest.importorskip("docx")
FIX = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.docx"


@pytest.fixture(scope="module")
def sample_docx():
    if not FIX.exists():
        FIX.parent.mkdir(parents=True, exist_ok=True)
        d = docx.Document()
        d.add_paragraph("hello from docx")
        d.save(FIX)
    return FIX


def test_docx_extracts(sample_docx):
    from chunkshop.sources.parsers.docx import DOCXParser

    assert "hello from docx" in DOCXParser().parse(sample_docx)


def test_docx_extensions():
    from chunkshop.sources.parsers.docx import DOCXParser

    assert "docx" in DOCXParser().supported_extensions


def test_docx_missing_lib_hint(monkeypatch, sample_docx):
    import sys

    monkeypatch.setitem(sys.modules, "docx", None)
    from chunkshop.sources.parsers.docx import DOCXParser

    with pytest.raises(RuntimeError, match=r"chunkshop\[docx\]"):
        DOCXParser().parse(sample_docx)
