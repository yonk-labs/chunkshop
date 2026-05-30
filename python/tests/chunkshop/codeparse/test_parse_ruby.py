"""Parse-test for ruby/sample.rb — symbols, methods, and orphan-free calls."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_ruby")

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id


def test_ruby_symbols(fixtures_dir: Path) -> None:
    result = parse_file(fixtures_dir / "ruby" / "sample.rb", language="ruby")
    assert result.parser == "tree-sitter"

    by_name = {(s.name, s.symbol_type): s for s in result.symbols}
    assert ("target", "function") in by_name
    assert ("Calculator", "class") in by_name
    assert ("Helpers", "class") in by_name  # module → class
    assert ("add", "method") in by_name
    assert ("multiply", "method") in by_name

    # 'add' is a method of Calculator (resolved from the enclosing class).
    assert by_name[("add", "method")].parent_name == "Calculator"
    assert by_name[("multiply", "method")].parent_name == "Calculator"


def test_ruby_no_orphan_callers(fixtures_dir: Path) -> None:
    path = fixtures_dir / "ruby" / "sample.rb"
    result = parse_file(path, language="ruby")
    sym_ids = {
        code_symbol_node_id("default", "ruby", str(path), s.fqn)
        for s in result.symbols
    }
    # target(a + b) is called from add(); it must attribute to add().
    target_calls = [c for c in result.call_sites if c.callee_name == "target"]
    assert target_calls, "no call to target() captured"

    add_fqn = next(
        s.fqn for s in result.symbols if s.name == "add" and s.symbol_type == "method"
    )
    add_id = code_symbol_node_id("default", "ruby", str(path), add_fqn)
    assert all(c.caller_node_id == add_id for c in target_calls)

    for c in result.call_sites:
        assert c.caller_node_id in sym_ids, f"orphan caller for {c.callee_name}"
