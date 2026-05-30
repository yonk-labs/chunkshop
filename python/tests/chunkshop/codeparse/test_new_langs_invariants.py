"""Cross-language invariants for the new codeparse extractors.

Parametrized over the Rust/C/C++/C#/Ruby fixtures: asserts the same graph
invariants sub-project B enforces for Python — no orphan caller node_ids,
in-bounds spans, real tree-sitter parse — so the orphan-edge bug cannot slip
into any new language. Skips per-language if that grammar isn't installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id

# (language tag, fixture relative path, grammar import to gate on)
_CASES = [
    ("rust", "rust/sample.rs", "tree_sitter_rust"),
    ("c", "c/sample.c", "tree_sitter_c"),
    ("cpp", "cpp/sample.cpp", "tree_sitter_cpp"),
    ("csharp", "csharp/Sample.cs", "tree_sitter_c_sharp"),
    ("ruby", "ruby/sample.rb", "tree_sitter_ruby"),
]


@pytest.mark.parametrize("lang,rel,grammar", _CASES)
def test_new_lang_no_orphans_and_in_bounds(
    fixtures_dir: Path, lang: str, rel: str, grammar: str
) -> None:
    pytest.importorskip(grammar)
    path = fixtures_dir / rel
    result = parse_file(path, language=lang)

    assert result.parser == "tree-sitter", f"{lang} fell back to regex"
    assert result.symbols, f"{lang} produced no symbols"

    # In-bounds spans.
    n_lines = len(path.read_text(encoding="utf-8").splitlines())
    for s in result.symbols:
        assert 1 <= s.line_start <= s.line_end <= max(n_lines, 1), (
            f"{lang}: {s.fqn} span=({s.line_start},{s.line_end}) "
            f"file_lines={n_lines}"
        )

    # No orphan caller node_ids — every call's caller is an emitted symbol.
    sym_ids = {
        code_symbol_node_id("default", lang, str(path), s.fqn)
        for s in result.symbols
    }
    for c in result.call_sites:
        assert c.caller_node_id in sym_ids, (
            f"{lang}: orphan caller for {c.callee_name} @L{c.line}"
        )
