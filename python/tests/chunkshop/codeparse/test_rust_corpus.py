"""Real-code corpus invariants for the Rust extractor.

Parses chunkshop's OWN ``rust/`` tree (a real, in-repo Rust codebase: ~120
files, ~1.3k symbols, ~9k calls) and asserts the same graph invariants the
Python corpus test enforces — no orphan caller node_ids, in-bounds spans, no
crashes. This is the "test against our own Rust projects" validation: it turns
the Rust extractor's correctness from asserted to proven on non-trivial code
(generics, traits, closures, macros, nested fns).

Skips cleanly if tree-sitter-rust isn't installed or the rust/ tree is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id

# python/tests/chunkshop/codeparse/ -> parents[4] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_RUST_ROOT = _REPO_ROOT / "rust"


def _rust_corpus() -> list[Path]:
    if not _RUST_ROOT.is_dir():
        pytest.skip(f"no rust/ tree at {_RUST_ROOT}")
    files = [
        p for p in _RUST_ROOT.rglob("*.rs")
        if "/target/" not in str(p)  # skip build artifacts
    ]
    if len(files) < 20:
        pytest.skip(f"rust corpus too small ({len(files)})")
    return sorted(files)


def test_rust_corpus_invariants() -> None:
    orphans: list[str] = []
    oob: list[str] = []
    sym_total = 0
    for path in _rust_corpus():
        result = parse_file(path, language="rust")  # must not raise
        n_lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        sym_total += len(result.symbols)
        ids = {
            code_symbol_node_id("default", "rust", str(path), s.fqn)
            for s in result.symbols
        }
        for s in result.symbols:
            if not (1 <= s.line_start <= s.line_end <= max(n_lines, 1)):
                oob.append(f"{path.name}: {s.fqn} ({s.line_start},{s.line_end})")
        for c in result.call_sites:
            if c.caller_node_id not in ids:
                orphans.append(f"{path.name}: {c.callee_name}@L{c.line}")

    assert sym_total > 200, f"corpus produced only {sym_total} symbols"
    assert not orphans, "orphan caller node_ids:\n" + "\n".join(orphans[:25])
    assert not oob, "out-of-bounds spans:\n" + "\n".join(oob[:25])
