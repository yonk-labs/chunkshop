"""Corpus-scale invariant tests for the codeparse layer.

Parses chunkshop's own source tree (hundreds of real symbols: nested
functions, decorators, methods) and asserts graph invariants that the tiny
per-language fixtures cannot reach. This is the regression net for the
orphan-edge bug (Risk 1) and span correctness (Risk 2).

Gated on the [code] extra — skips cleanly when tree-sitter isn't installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_python")

import chunkshop
from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id

_SRC_ROOT = Path(chunkshop.__file__).resolve().parent


def _python_corpus() -> list[Path]:
    files = sorted(_SRC_ROOT.rglob("*.py"))
    assert len(files) > 30, f"corpus too small ({len(files)}); wrong root?"
    return files


def test_no_orphan_caller_nodes() -> None:
    """Every call site's caller_node_id must be an emitted symbol's node_id.

    A caller_node_id with no matching symbol means the edge's source node
    doesn't exist — the orphan-edge bug. project_id/language/file_path here
    mirror exactly what the extractor used to mint caller_node_id.
    """
    offenders: list[str] = []
    for path in _python_corpus():
        result = parse_file(path, language="python")
        lang = result.language or "python"
        fp = str(path)
        symbol_ids = {
            code_symbol_node_id("default", lang, fp, s.fqn)
            for s in result.symbols
        }
        for cs in result.call_sites:
            if cs.caller_node_id not in symbol_ids:
                offenders.append(
                    f"{path.name}: {cs.caller_node_id} ({cs.callee_name} @L{cs.line})"
                )
    assert not offenders, "orphan caller node_ids:\n" + "\n".join(offenders[:25])


def test_spans_in_bounds() -> None:
    """1 <= line_start <= line_end <= len(file_lines) for every symbol."""
    offenders: list[str] = []
    for path in _python_corpus():
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        result = parse_file(path, language="python")
        for s in result.symbols:
            if not (1 <= s.line_start <= s.line_end <= max(n_lines, 1)):
                offenders.append(
                    f"{path.name}: {s.fqn} span=({s.line_start},{s.line_end}) file_lines={n_lines}"
                )
    assert not offenders, "out-of-bounds spans:\n" + "\n".join(offenders[:25])


def test_parse_never_raises() -> None:
    """parse_file is best-effort: it must not raise on any corpus file."""
    for path in _python_corpus():
        parse_file(path, language="python")  # must not raise


def test_node_ids_deterministic() -> None:
    """Re-parsing a file yields identical (fqn, node_id) sets."""
    sample = _python_corpus()[0]

    def ids(p: Path) -> set[tuple[str, str]]:
        r = parse_file(p, language="python")
        return {
            (s.fqn, code_symbol_node_id("default", r.language or "python", str(p), s.fqn))
            for s in r.symbols
        }

    assert ids(sample) == ids(sample)
