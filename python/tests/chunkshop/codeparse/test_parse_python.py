"""Parse-test for python/sample.py — exact symbol + call site assertions."""
from __future__ import annotations

from pathlib import Path

from chunkshop.codeparse import parse_file


def test_python_sample_symbols(fixtures_dir: Path) -> None:
    """sample.py has exactly: 1 free function, 1 class, 2 methods."""
    result = parse_file(fixtures_dir / "python" / "sample.py", language="python")

    names_types = [(s.name, s.symbol_type) for s in result.symbols]
    assert ("helper", "function") in names_types
    assert ("Calculator", "class") in names_types
    assert ("add", "method") in names_types
    assert ("multiply", "method") in names_types

    # Exactly 4 symbols, no nested-function leak, no doubles.
    assert len(result.symbols) == 4

    # Methods carry the right parent_name.
    methods = {s.name: s for s in result.symbols if s.symbol_type == "method"}
    assert methods["add"].parent_name == "Calculator"
    assert methods["multiply"].parent_name == "Calculator"

    # Free function has no parent.
    helper = next(s for s in result.symbols if s.name == "helper")
    assert helper.parent_name is None
    assert helper.symbol_type == "function"


def test_python_sample_call_sites(fixtures_dir: Path) -> None:
    """add() calls helper(); we should see at least one CallSite for it."""
    result = parse_file(fixtures_dir / "python" / "sample.py", language="python")
    helper_calls = [c for c in result.call_sites if c.callee_name == "helper"]
    assert len(helper_calls) >= 1
    # The call site lives inside add(), which is a method whose sibling is
    # helper() in the same file -> resolved_intra_file is True.
    assert any(c.resolved_intra_file for c in helper_calls)


def test_python_sample_line_ranges_are_inclusive(fixtures_dir: Path) -> None:
    """Tree-sitter line ranges must cover the full body (start <= end)."""
    result = parse_file(fixtures_dir / "python" / "sample.py", language="python")
    cls = next(s for s in result.symbols if s.symbol_type == "class")
    # The Calculator class body spans multiple lines.
    assert cls.line_end > cls.line_start
