"""Prove Go/Java orphan-safety (previously only asserted by reasoning).

Go and Java have no nested *function/method declarations*, so sub-project B
left their ``_enclosing_function`` unchanged and claimed they were
structurally safe. These tests make that claim evidence: a call inside a Go
closure / Java lambda must attribute to the enclosing emitted symbol, not an
orphan, and spans stay in bounds.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id

_CASES = [
    ("go", "go/closures.go", "tree_sitter_go", "runner", "target"),
    ("java", "java/Lambdas.java", "tree_sitter_java", "runner", "target"),
]


@pytest.mark.parametrize("lang,rel,grammar,caller,callee", _CASES)
def test_closure_call_is_not_orphan(
    fixtures_dir: Path, lang: str, rel: str, grammar: str, caller: str, callee: str
) -> None:
    pytest.importorskip(grammar)
    path = fixtures_dir / rel
    result = parse_file(path, language=lang)
    assert result.parser == "tree-sitter"

    n_lines = len(path.read_text(encoding="utf-8").splitlines())
    sym_ids = {
        code_symbol_node_id("default", lang, str(path), s.fqn)
        for s in result.symbols
    }
    for s in result.symbols:
        assert 1 <= s.line_start <= s.line_end <= max(n_lines, 1)

    # The call to `callee` lives inside a closure/lambda inside `caller`.
    target_calls = [c for c in result.call_sites if c.callee_name == callee]
    assert target_calls, f"{lang}: no call to {callee} captured"
    for c in result.call_sites:
        assert c.caller_node_id in sym_ids, (
            f"{lang}: orphan caller for {c.callee_name}@L{c.line}"
        )
