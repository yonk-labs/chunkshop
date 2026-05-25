"""Parse-test for javascript/sample.js — regex-only path."""
from __future__ import annotations

from pathlib import Path

from chunkshop.codeparse import parse_file


def test_javascript_sample_symbols(fixtures_dir: Path) -> None:
    """sample.js has 1 free function (helper), 1 class (Calculator), 2 methods."""
    result = parse_file(
        fixtures_dir / "javascript" / "sample.js", language="javascript"
    )

    names_types = {(s.name, s.symbol_type) for s in result.symbols}
    assert ("helper", "function") in names_types
    assert ("Calculator", "class") in names_types
    method_names = {s.name for s in result.symbols if s.symbol_type == "method"}
    assert "add" in method_names
    assert "multiply" in method_names

    assert result.parser == "regex"


def test_javascript_sample_call_sites(fixtures_dir: Path) -> None:
    """add() invokes helper()."""
    result = parse_file(
        fixtures_dir / "javascript" / "sample.js", language="javascript"
    )
    helper_calls = [c for c in result.call_sites if c.callee_name == "helper"]
    assert len(helper_calls) >= 1
