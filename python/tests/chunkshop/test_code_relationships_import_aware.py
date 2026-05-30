"""Sub-project C: import-aware cross-file resolution.

Ambiguous name matches (multiple candidate FQNs) are narrowed to the candidate
whose module the caller file imports, emitting a single precise edge tagged
``resolution="import_resolved"`` instead of an N-way fan-out.
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_python")

from chunkshop.config import CodeRelationshipsExtractor as Cfg
from chunkshop.extractors.code_relationships import (
    CodeRelationshipsExtractor,
    _import_tokens,
)


# --- _import_tokens ---------------------------------------------------------


def test_import_tokens_python() -> None:
    toks = _import_tokens(["from foo.bar import helper", "import os"])
    assert {"foo", "bar", "helper", "os"} <= toks


def test_import_tokens_rust_and_c() -> None:
    assert "calc" in _import_tokens(['#include "calc.h"'])
    assert {"crate", "a", "b"} <= _import_tokens(["use crate::a::b;"])


def test_import_tokens_empty() -> None:
    assert _import_tokens([]) == set()


# --- narrowing (added in Task 2) -------------------------------------------

_DEF_A = "def helper(v):\n    return v * 2\n"
_DEF_B = "def helper(v):\n    return v\n"


def _caller(import_line: str) -> str:
    return f"{import_line}\n\ndef run(v):\n    return helper(v)\n"


def test_ambiguous_narrows_to_imported_candidate() -> None:
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_DEF_A, language="python", source_path="a.py")
    ext.extract(_DEF_B, language="python", source_path="b.py")
    ext.extract(_caller("from a import helper"), language="python", source_path="c.py")
    edges = ext.finalize(project_id="t")
    helper_edges = [
        e for e in edges
        if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")
    ]
    assert len(helper_edges) == 1
    assert helper_edges[0]["dst_fqn"] == "a.helper"
    assert helper_edges[0]["evidence"]["resolution"] == "import_resolved"
    assert helper_edges[0]["provenance"] == "heuristic"


def test_no_import_keeps_ambiguous_fanout() -> None:
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_DEF_A, language="python", source_path="a.py")
    ext.extract(_DEF_B, language="python", source_path="b.py")
    ext.extract(_caller("# no import"), language="python", source_path="c.py")
    edges = ext.finalize(project_id="t")
    helper_edges = [
        e for e in edges
        if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")
    ]
    assert len(helper_edges) == 2
    assert all(e["evidence"]["resolution"] == "ambiguous_name" for e in helper_edges)


def test_two_supported_keeps_fanout() -> None:
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_DEF_A, language="python", source_path="a.py")
    ext.extract(_DEF_B, language="python", source_path="b.py")
    ext.extract(
        _caller("from a import helper\nfrom b import helper as h2"),
        language="python",
        source_path="c.py",
    )
    edges = ext.finalize(project_id="t")
    helper_edges = [
        e for e in edges
        if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")
    ]
    assert len(helper_edges) == 2


# --- class-edge narrowing (INHERITS) ---------------------------------------

_BASE_A = "class Base:\n    pass\n"
_BASE_B = "class Base:\n    pass\n"


def test_ambiguous_inherits_narrows_to_imported_base() -> None:
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_BASE_A, language="python", source_path="a.py")
    ext.extract(_BASE_B, language="python", source_path="b.py")
    ext.extract(
        "from a import Base\n\nclass Sub(Base):\n    pass\n",
        language="python",
        source_path="c.py",
    )
    edges = ext.finalize(project_id="t")
    inherits = [
        e for e in edges
        if e["edge_type"] == "INHERITS" and e["dst_fqn"].endswith(".Base")
    ]
    assert len(inherits) == 1
    assert inherits[0]["dst_fqn"] == "a.Base"
    assert inherits[0]["evidence"]["resolution"] == "import_resolved"


def test_ambiguous_inherits_no_import_keeps_fanout() -> None:
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_BASE_A, language="python", source_path="a.py")
    ext.extract(_BASE_B, language="python", source_path="b.py")
    ext.extract(
        "class Sub(Base):\n    pass\n", language="python", source_path="c.py"
    )
    edges = ext.finalize(project_id="t")
    inherits = [
        e for e in edges
        if e["edge_type"] == "INHERITS" and e["dst_fqn"].endswith(".Base")
    ]
    assert len(inherits) == 2
    assert all(e["evidence"]["resolution"] == "ambiguous_name" for e in inherits)
