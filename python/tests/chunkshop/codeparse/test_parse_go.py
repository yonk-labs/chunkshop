"""Parse-test for go/sample.go — regex-only path."""
from __future__ import annotations

from pathlib import Path

from chunkshop.codeparse import parse_file


def test_go_sample_symbols(fixtures_dir: Path) -> None:
    """sample.go has 1 struct (Calculator), 2 methods (Add/Multiply), 1 func (helper)."""
    result = parse_file(fixtures_dir / "go" / "sample.go", language="go")

    names = {s.name for s in result.symbols}
    assert "Calculator" in names
    assert "Add" in names
    assert "Multiply" in names
    assert "helper" in names

    # The regex extractor is regex-only -> parser tag must be "regex".
    assert result.parser == "regex"


def test_go_sample_call_sites(fixtures_dir: Path) -> None:
    """Add() calls helper(); regex extractor still surfaces it."""
    result = parse_file(fixtures_dir / "go" / "sample.go", language="go")
    helper_calls = [c for c in result.call_sites if c.callee_name == "helper"]
    assert len(helper_calls) >= 1
