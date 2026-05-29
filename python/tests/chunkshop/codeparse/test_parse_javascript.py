"""Parse-test for javascript/sample.js.

Coverage assertions are parser-agnostic; tree-sitter-specific improvements
live in ``test_javascript_treesitter_*`` and skip when the grammar is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

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


def test_javascript_sample_call_sites(fixtures_dir: Path) -> None:
    """add() invokes helper(); found on either path."""
    result = parse_file(
        fixtures_dir / "javascript" / "sample.js", language="javascript"
    )
    helper_calls = [c for c in result.call_sites if c.callee_name == "helper"]
    assert len(helper_calls) >= 1


# --- tree-sitter path: assert improvements over the regex baseline ---------


def test_javascript_treesitter_method_parent_and_count(fixtures_dir: Path) -> None:
    """tree-sitter resolves method parent classes and emits no spurious symbols."""
    pytest.importorskip("tree_sitter_javascript")
    result = parse_file(
        fixtures_dir / "javascript" / "sample.js", language="javascript"
    )

    assert result.parser == "tree-sitter"
    methods = {s.name: s for s in result.symbols if s.symbol_type == "method"}
    assert methods["add"].parent_name == "Calculator"
    assert methods["multiply"].parent_name == "Calculator"
    # Exactly: helper, Calculator, add, multiply.
    assert len(result.symbols) == 4


def test_javascript_treesitter_line_ranges_span_bodies(fixtures_dir: Path) -> None:
    """The class body spans multiple lines — regex collapsed it to one."""
    pytest.importorskip("tree_sitter_javascript")
    result = parse_file(
        fixtures_dir / "javascript" / "sample.js", language="javascript"
    )
    calc = next(s for s in result.symbols if s.name == "Calculator")
    assert calc.line_end > calc.line_start
