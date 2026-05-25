import pytest
from pathlib import Path

openpyxl = pytest.importorskip("openpyxl")
FIX = Path(__file__).parents[1] / "fixtures" / "parsers" / "sample.xlsx"


@pytest.fixture(scope="module")
def sample_xlsx():
    if not FIX.exists():
        FIX.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "cell value"
        wb.save(FIX)
    return FIX


def test_xlsx_extracts(sample_xlsx):
    from chunkshop.sources.parsers.xlsx import XLSXParser

    assert "cell value" in XLSXParser().parse(sample_xlsx)


def test_xlsx_extensions():
    from chunkshop.sources.parsers.xlsx import XLSXParser

    assert "xlsx" in XLSXParser().supported_extensions


def test_xlsx_missing_lib_hint(monkeypatch, sample_xlsx):
    import sys

    monkeypatch.setitem(sys.modules, "openpyxl", None)
    from chunkshop.sources.parsers.xlsx import XLSXParser

    with pytest.raises(RuntimeError, match=r"chunkshop\[xlsx\]"):
        XLSXParser().parse(sample_xlsx)
