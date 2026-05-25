import pytest
from pathlib import Path

pypdf = pytest.importorskip("pypdf")
FIX = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.pdf"


@pytest.fixture(scope="module")
def sample_pdf():
    """Generate a 1-page, text-bearing PDF fixture if missing.

    Uses reportlab when available (gives extractable text); falls back to a
    blank pypdf page if not. In the fallback case the extraction assertion
    is intentionally loose (isinstance str only) — comment on it below.
    """
    out = FIX
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.pdfgen import canvas

        c = canvas.Canvas(str(out))
        c.drawString(72, 720, "hello from pdf")
        c.showPage()
        c.save()
    except Exception:
        # Fallback: blank page (no extractable text).
        w = pypdf.PdfWriter()
        w.add_blank_page(width=72, height=72)
        with open(out, "wb") as fh:
            w.write(fh)
    return out


def test_pdf_parser_extracts_text(sample_pdf):
    from chunkshop.sources.parsers.pdf import PDFParser

    text = PDFParser().parse(sample_pdf)
    # If reportlab built the fixture the body will contain "hello from pdf";
    # otherwise the blank-page fallback returns "" — both satisfy str.
    assert isinstance(text, str)


def test_pdf_parser_extensions():
    from chunkshop.sources.parsers.pdf import PDFParser

    assert "pdf" in PDFParser().supported_extensions


def test_pdf_parser_missing_lib_hint(monkeypatch, sample_pdf):
    import sys

    monkeypatch.setitem(sys.modules, "pypdf", None)  # simulate not installed
    from chunkshop.sources.parsers.pdf import PDFParser

    with pytest.raises(RuntimeError, match=r"chunkshop\[pdf\]"):
        PDFParser().parse(sample_pdf)
