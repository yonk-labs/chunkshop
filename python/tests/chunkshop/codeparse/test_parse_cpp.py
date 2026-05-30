"""Parse-test for cpp/sample.cpp — symbols, methods, and orphan-free calls."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_cpp")

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id


def test_cpp_symbols(fixtures_dir: Path) -> None:
    result = parse_file(fixtures_dir / "cpp" / "sample.cpp", language="cpp")
    assert result.parser == "tree-sitter"

    by_name = {(s.name, s.symbol_type): s for s in result.symbols}
    assert ("target", "function") in by_name
    assert ("Calculator", "class") in by_name  # class_specifier
    assert ("add", "method") in by_name

    # 'add' is an inline method of Calculator.
    assert by_name[("add", "method")].parent_name == "Calculator"


def test_cpp_no_orphan_callers(fixtures_dir: Path) -> None:
    path = fixtures_dir / "cpp" / "sample.cpp"
    result = parse_file(path, language="cpp")
    sym_ids = {
        code_symbol_node_id("default", "cpp", str(path), s.fqn)
        for s in result.symbols
    }
    # target(x) is called from a lambda inside add(); it must attribute to add().
    target_calls = [c for c in result.call_sites if c.callee_name == "target"]
    assert target_calls, "no call to target() captured"
    for c in result.call_sites:
        assert c.caller_node_id in sym_ids, f"orphan caller for {c.callee_name}"
