"""Parse-test for rust/sample.rs — symbols, methods, and orphan-free calls."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id


def test_rust_symbols(fixtures_dir: Path) -> None:
    result = parse_file(fixtures_dir / "rust" / "sample.rs", language="rust")
    assert result.parser == "tree-sitter"

    by_name = {(s.name, s.symbol_type): s for s in result.symbols}
    assert ("target", "function") in by_name
    assert ("Calculator", "class") in by_name  # struct_item
    assert ("Op", "interface") in by_name  # trait_item
    assert ("add", "method") in by_name

    # 'add' is a method of Calculator (resolved from the impl type).
    assert by_name[("add", "method")].parent_name == "Calculator"

    # 'helper' is nested inside add() and must NOT be emitted (one level deep).
    assert "helper" not in {s.name for s in result.symbols}
    # Trait method signatures (no body) are not emitted (interface marker only).
    assert "apply" not in {s.name for s in result.symbols}
    # But a trait DEFAULT method (with a body) IS emitted, parented to the trait,
    # so the calls inside its body have a real caller symbol (no orphan edges).
    assert ("twice", "method") in by_name
    assert by_name[("twice", "method")].parent_name == "Op"


def test_rust_no_orphan_callers(fixtures_dir: Path) -> None:
    path = fixtures_dir / "rust" / "sample.rs"
    result = parse_file(path, language="rust")
    sym_ids = {
        code_symbol_node_id("default", "rust", str(path), s.fqn)
        for s in result.symbols
    }
    # target(x) is called from the nested helper(); it must attribute to add().
    target_calls = [c for c in result.call_sites if c.callee_name == "target"]
    assert target_calls, "no call to target() captured"
    for c in result.call_sites:
        assert c.caller_node_id in sym_ids, f"orphan caller for {c.callee_name}"
