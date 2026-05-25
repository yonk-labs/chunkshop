from pathlib import Path

from chunkshop.sources.parsers import DEFAULT_PARSERS, get_parser
from chunkshop.sources.parsers.text import TextParser


def test_known_extensions_resolve():
    assert get_parser("pdf").__class__.__name__ == "PDFParser"
    assert get_parser("DOCX").__class__.__name__ == "DOCXParser"  # case-insensitive


def test_leading_dot_is_stripped():
    assert get_parser(".pdf").__class__.__name__ == "PDFParser"


def test_unknown_extension_falls_back_to_text():
    assert isinstance(get_parser("xyz"), TextParser)


def test_empty_extension_falls_back_to_text():
    assert isinstance(get_parser(""), TextParser)


def test_custom_parsers_override():
    class _Mine:
        supported_extensions = ["pdf"]

        def parse(self, path):
            return "mine"

    p = get_parser("pdf", parsers={"pdf": _Mine()})
    assert p.parse(Path(".")) == "mine"


def test_default_parsers_covers_all_formats():
    for ext in ["pdf", "docx", "pptx", "xlsx", "html", "htm", "txt", "md"]:
        assert ext in DEFAULT_PARSERS
