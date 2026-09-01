from pathlib import Path

import pytest

from chunkshop.sources.parsers.base import call_deriver
from chunkshop.sources.parsers.result import ParseResult, normalize_parse_result


def test_parse_result_holds_text_and_metadata():
    r = ParseResult(text="hello", metadata={"width": 8})
    assert r.text == "hello"
    assert r.metadata == {"width": 8}


def test_parse_result_metadata_defaults_to_empty_dict():
    assert ParseResult(text="hi").metadata == {}


def test_normalize_wraps_a_bare_string():
    """Old-style parsers return str. They must keep working."""
    r = normalize_parse_result("legacy output")
    assert isinstance(r, ParseResult)
    assert r.text == "legacy output"
    assert r.metadata == {}


def test_normalize_passes_through_a_parse_result():
    original = ParseResult(text="x", metadata={"a": 1})
    assert normalize_parse_result(original) is original


def test_normalize_rejects_other_types():
    with pytest.raises(TypeError, match="str or ParseResult"):
        normalize_parse_result(42)


def test_legacy_parse_only_object_still_works(tmp_path):
    """A parser written against chunkshop 1.0 must work with NO edits."""

    class LegacyParser:
        supported_extensions = ["weird"]

        def parse(self, path: Path) -> str:
            return path.read_text().upper()

    p = tmp_path / "a.weird"
    p.write_text("raw", encoding="utf-8")
    result = call_deriver(LegacyParser(), p)
    assert result.text == "RAW"
    assert result.metadata == {}


def test_new_style_deriver_returns_metadata(tmp_path):
    class MetaDeriver:
        supported_extensions = ["weird"]

        def derive(self, path: Path) -> ParseResult:
            return ParseResult(text="body", metadata={"kind": "weird"})

    p = tmp_path / "a.weird"
    p.write_text("x", encoding="utf-8")
    result = call_deriver(MetaDeriver(), p)
    assert result.text == "body"
    assert result.metadata == {"kind": "weird"}


def test_object_with_neither_method_is_rejected(tmp_path):
    class Broken:
        supported_extensions = ["weird"]

    with pytest.raises(TypeError, match="neither"):
        call_deriver(Broken(), tmp_path / "a.weird")


def test_derive_is_preferred_over_parse_when_both_exist(tmp_path):
    """Binding global constraint: call_deriver must prefer .derive() over
    .parse() when an object implements both. The two methods return clearly
    distinguishable values so a reversed operand order fails loudly."""

    class Both:
        supported_extensions = ["weird"]

        def derive(self, path: Path) -> ParseResult:
            return ParseResult(text="FROM DERIVE", metadata={"via": "derive"})

        def parse(self, path: Path) -> str:
            return "FROM PARSE"

    p = tmp_path / "a.weird"
    p.write_text("x", encoding="utf-8")
    result = call_deriver(Both(), p)
    assert result.text == "FROM DERIVE"
    assert result.metadata == {"via": "derive"}
