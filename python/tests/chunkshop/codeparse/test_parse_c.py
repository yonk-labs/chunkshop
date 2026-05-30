"""Parse-test for c/sample.c — symbols and orphan-free calls."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_c")

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id


def test_c_symbols(fixtures_dir: Path) -> None:
    result = parse_file(fixtures_dir / "c" / "sample.c", language="c")
    assert result.parser == "tree-sitter"

    by_name = {(s.name, s.symbol_type) for s in result.symbols}
    assert ("target", "function") in by_name
    assert ("caller", "function") in by_name
    assert ("Calculator", "class") in by_name  # struct_specifier


def test_c_no_orphan_callers(fixtures_dir: Path) -> None:
    path = fixtures_dir / "c" / "sample.c"
    result = parse_file(path, language="c")
    sym_ids = {
        code_symbol_node_id("default", "c", str(path), s.fqn)
        for s in result.symbols
    }
    # target(v) is called from caller(); it must attribute to caller().
    target_calls = [c for c in result.call_sites if c.callee_name == "target"]
    assert target_calls, "no call to target() captured"
    caller_fqn = next(s.fqn for s in result.symbols if s.name == "caller")
    caller_id = code_symbol_node_id("default", "c", str(path), caller_fqn)
    assert all(c.caller_node_id == caller_id for c in target_calls)
    for c in result.call_sites:
        assert c.caller_node_id in sym_ids, f"orphan caller for {c.callee_name}"
