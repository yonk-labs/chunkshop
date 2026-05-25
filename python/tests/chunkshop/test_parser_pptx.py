import pytest
from pathlib import Path

pptx = pytest.importorskip("pptx")
FIX = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.pptx"


@pytest.fixture(scope="module")
def sample_pptx():
    if not FIX.exists():
        FIX.parent.mkdir(parents=True, exist_ok=True)
        prs = pptx.Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = "deck title"
        prs.save(FIX)
    return FIX


def test_pptx_extracts(sample_pptx):
    from chunkshop.sources.parsers.pptx import PPTXParser

    assert "deck title" in PPTXParser().parse(sample_pptx)


def test_pptx_extensions():
    from chunkshop.sources.parsers.pptx import PPTXParser

    assert "pptx" in PPTXParser().supported_extensions


def test_pptx_missing_lib_hint(monkeypatch, sample_pptx):
    import sys

    monkeypatch.setitem(sys.modules, "pptx", None)
    from chunkshop.sources.parsers.pptx import PPTXParser

    with pytest.raises(RuntimeError, match=r"chunkshop\[pptx\]"):
        PPTXParser().parse(sample_pptx)
