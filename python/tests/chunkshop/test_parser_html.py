import pytest

bs4 = pytest.importorskip("bs4")


def test_html_strips_tags(tmp_path):
    from chunkshop.sources.parsers.html import HTMLParser

    p = tmp_path / "a.html"
    p.write_text(
        "<html><body><h1>Title</h1><p>Body text</p>"
        "<script>ignore()</script></body></html>",
        encoding="utf-8",
    )
    out = HTMLParser().parse(p)
    assert "Title" in out and "Body text" in out
    assert "ignore" not in out  # script/style removed


def test_html_extensions():
    from chunkshop.sources.parsers.html import HTMLParser

    assert "html" in HTMLParser().supported_extensions
    assert "htm" in HTMLParser().supported_extensions


def test_html_missing_lib_hint(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "bs4", None)
    from chunkshop.sources.parsers.html import HTMLParser

    p = tmp_path / "a.html"
    p.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"chunkshop\[html\]"):
        HTMLParser().parse(p)
