from pathlib import Path

import pytest

from chunkshop.sources.parsers.base import FileParser, ParserError
from chunkshop.sources.parsers.text import TextParser


def test_text_parser_implements_protocol():
    assert isinstance(TextParser(), FileParser)


def test_text_parser_reads(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello world", encoding="utf-8")
    out = TextParser().parse(p)
    assert out == "hello world"


def test_text_parser_extensions():
    assert "txt" in TextParser().supported_extensions


def test_parser_error_is_exception():
    with pytest.raises(ParserError):
        raise ParserError("bad file")
