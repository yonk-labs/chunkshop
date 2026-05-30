"""Risk 3: realistic fixtures with nesting + decorators, exact attribution."""
from __future__ import annotations

from pathlib import Path

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id


def _sym_ids(result, lang: str, fp: str) -> set[str]:
    return {code_symbol_node_id("default", lang, fp, s.fqn) for s in result.symbols}


def test_python_realistic_no_orphans_and_decorator_span(fixtures_dir: Path) -> None:
    path = fixtures_dir / "python" / "realistic.py"
    result = parse_file(path, language="python")

    names = {s.name for s in result.symbols}
    # 'step' is nested in run() -> never emitted.
    assert "step" not in names
    assert {"load", "cached_double", "Pipeline", "run"} <= names

    # The decorator widens cached_double's span to its @-line.
    cd = next(s for s in result.symbols if s.name == "cached_double")
    line = path.read_text().splitlines()
    assert line[cd.line_start - 1].lstrip().startswith("@")

    # No orphan callers (cached_double() is called from nested step()).
    ids = _sym_ids(result, "python", str(path))
    for c in result.call_sites:
        assert c.caller_node_id in ids


def test_typescript_realistic_no_orphans(fixtures_dir: Path) -> None:
    path = fixtures_dir / "typescript" / "realistic.ts"
    result = parse_file(path, language="typescript")
    names = {s.name for s in result.symbols}
    assert "step" not in names  # nested fn not emitted
    ids = _sym_ids(result, "typescript", str(path))
    for c in result.call_sites:
        assert c.caller_node_id in ids
