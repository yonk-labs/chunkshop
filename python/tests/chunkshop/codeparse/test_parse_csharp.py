"""Parse-test for csharp/Sample.cs — symbols, methods, and orphan-free calls."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_c_sharp")

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id


def test_csharp_symbols(fixtures_dir: Path) -> None:
    result = parse_file(fixtures_dir / "csharp" / "Sample.cs", language="csharp")
    assert result.parser == "tree-sitter"

    by_name = {(s.name, s.symbol_type): s for s in result.symbols}
    assert ("Calculator", "class") in by_name
    assert ("IOp", "interface") in by_name
    assert ("Add", "method") in by_name
    assert ("Target", "method") in by_name

    # 'Add' / 'Target' are methods of Calculator (resolved from the class body).
    assert by_name[("Add", "method")].parent_name == "Calculator"
    assert by_name[("Target", "method")].parent_name == "Calculator"

    names = {s.name for s in result.symbols}
    # 'Helper' is a local function inside Add() — must NOT be emitted.
    assert "Helper" not in names
    # Interface method signatures are not emitted (interface marker only).
    assert "Apply" not in names


def test_csharp_no_orphan_callers(fixtures_dir: Path) -> None:
    path = fixtures_dir / "csharp" / "Sample.cs"
    result = parse_file(path, language="csharp")
    sym_ids = {
        code_symbol_node_id("default", "csharp", str(path), s.fqn)
        for s in result.symbols
    }
    # Target(x) is called from the nested local function Helper(); it must
    # attribute to Add() — never to the (un-emitted) local function.
    target_calls = [c for c in result.call_sites if c.callee_name == "Target"]
    assert target_calls, "no call to Target() captured"
    for c in result.call_sites:
        assert c.caller_node_id in sym_ids, f"orphan caller for {c.callee_name}"
