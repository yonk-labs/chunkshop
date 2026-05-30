"""Real-code corpus invariants for the C extractor (env-gated).

CI has no large C codebase vendored (and Postgres is too big to bundle), so
this test is gated on the ``CHUNKSHOP_C_CORPUS`` env var pointing at a C source
tree. Point it at e.g. an extracted Postgres release to validate the C
extractor against gnarly real-world code:

    curl -sSL https://ftp.postgresql.org/pub/source/v16.3/postgresql-16.3.tar.gz | tar xz
    CHUNKSHOP_C_CORPUS=$PWD/postgresql-16.3/src \\
      uv run --no-sync pytest tests/chunkshop/codeparse/test_c_corpus.py -v

Validated manually 2026-05-30 against Postgres 16.3 src/ (1269 .c files):
25,431 symbols, 250,000 calls, 0 orphans, 0 out-of-bounds, 0 crashes, 0 regex
fallback. Skips cleanly when the env var is unset (CI default).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_c")

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id

_CORPUS_ENV = "CHUNKSHOP_C_CORPUS"


def _c_corpus() -> list[Path]:
    root = os.environ.get(_CORPUS_ENV)
    if not root:
        pytest.skip(f"set {_CORPUS_ENV}=/path/to/c/src to run the C corpus test")
    root_path = Path(root)
    if not root_path.is_dir():
        pytest.skip(f"{_CORPUS_ENV}={root} is not a directory")
    return sorted(root_path.rglob("*.c"))


def test_c_corpus_invariants() -> None:
    files = _c_corpus()
    assert files, "C corpus has no .c files"
    orphans: list[str] = []
    oob: list[str] = []
    crashes: list[str] = []
    sym_total = 0
    for path in files:
        try:
            result = parse_file(path, language="c")
        except Exception as exc:  # pragma: no cover — proves "never raises"
            crashes.append(f"{path.name}: {exc}")
            continue
        if result.parser != "tree-sitter":
            continue  # regex fallback (grammar absent) — not a correctness check
        n_lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        sym_total += len(result.symbols)
        ids = {
            code_symbol_node_id("default", "c", str(path), s.fqn)
            for s in result.symbols
        }
        for s in result.symbols:
            if not (1 <= s.line_start <= s.line_end <= max(n_lines, 1)):
                oob.append(f"{path.name}: {s.fqn} ({s.line_start},{s.line_end})")
        for c in result.call_sites:
            if c.caller_node_id not in ids:
                orphans.append(f"{path.name}: {c.callee_name}@L{c.line}")

    assert not crashes, "parse crashes:\n" + "\n".join(crashes[:25])
    assert not orphans, "orphan caller node_ids:\n" + "\n".join(orphans[:25])
    assert not oob, "out-of-bounds spans:\n" + "\n".join(oob[:25])
    assert sym_total > 0
