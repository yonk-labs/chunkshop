"""Parse-test for go/sample.go.

Coverage assertions are parser-agnostic (they hold for both the tree-sitter
and the regex-fallback paths). The tree-sitter-specific improvements live in
``test_go_treesitter_*`` which skip cleanly when ``tree_sitter_go`` is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chunkshop.codeparse import parse_file


def test_go_sample_symbols(fixtures_dir: Path) -> None:
    """sample.go has 1 struct (Calculator), 2 methods (Add/Multiply), 1 func (helper)."""
    result = parse_file(fixtures_dir / "go" / "sample.go", language="go")

    names = {s.name for s in result.symbols}
    assert "Calculator" in names
    assert "Add" in names
    assert "Multiply" in names
    assert "helper" in names


def test_go_sample_call_sites(fixtures_dir: Path) -> None:
    """Add() calls helper(); the extractor surfaces it on either path."""
    result = parse_file(fixtures_dir / "go" / "sample.go", language="go")
    helper_calls = [c for c in result.call_sites if c.callee_name == "helper"]
    assert len(helper_calls) >= 1


# --- tree-sitter path: assert improvements over the regex baseline ---------


def test_go_treesitter_resolves_method_receiver(fixtures_dir: Path) -> None:
    """With tree-sitter, Go methods are typed as 'method' and carry the receiver
    type as parent_name — the regex baseline could only emit them as parentless
    functions."""
    pytest.importorskip("tree_sitter_go")
    result = parse_file(fixtures_dir / "go" / "sample.go", language="go")

    assert result.parser == "tree-sitter"
    methods = {s.name: s for s in result.symbols if s.symbol_type == "method"}
    assert "Add" in methods and "Multiply" in methods
    assert methods["Add"].parent_name == "Calculator"
    assert methods["Multiply"].parent_name == "Calculator"

    # Struct type is a 'class' symbol; free function is parentless.
    calc = next(s for s in result.symbols if s.name == "Calculator")
    assert calc.symbol_type == "class"
    helper = next(s for s in result.symbols if s.name == "helper")
    assert helper.symbol_type == "function"
    assert helper.parent_name is None


def test_go_treesitter_line_ranges_span_bodies(fixtures_dir: Path) -> None:
    """tree-sitter reports real inclusive line spans; the regex baseline
    collapsed every symbol to a single line."""
    pytest.importorskip("tree_sitter_go")
    result = parse_file(fixtures_dir / "go" / "sample.go", language="go")
    add = next(s for s in result.symbols if s.name == "Add")
    assert add.line_end > add.line_start
