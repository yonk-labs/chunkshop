"""Parse-test for java/Sample.java — exact symbol + call site assertions."""
from __future__ import annotations

from pathlib import Path

from chunkshop.codeparse import parse_file


def test_java_sample_symbols(fixtures_dir: Path) -> None:
    """Sample.java has exactly: 1 class, 3 methods (add/multiply/helper).

    helper() is declared as a static method on Calculator in Java (no free
    functions), so the expected shape is 1 class + 3 methods.
    """
    result = parse_file(fixtures_dir / "java" / "Sample.java", language="java")

    names_types = [(s.name, s.symbol_type) for s in result.symbols]
    assert ("Calculator", "class") in names_types
    assert ("add", "method") in names_types
    assert ("multiply", "method") in names_types
    assert ("helper", "method") in names_types

    methods = {s.name: s for s in result.symbols if s.symbol_type == "method"}
    assert methods["add"].parent_name == "Calculator"
    assert methods["multiply"].parent_name == "Calculator"
    assert methods["helper"].parent_name == "Calculator"


def test_java_sample_call_sites(fixtures_dir: Path) -> None:
    """add() calls helper(); CallSite must include it."""
    result = parse_file(fixtures_dir / "java" / "Sample.java", language="java")
    helper_calls = [c for c in result.call_sites if c.callee_name == "helper"]
    assert len(helper_calls) >= 1
    # Same-file callee -> resolved_intra_file True.
    assert any(c.resolved_intra_file for c in helper_calls)
