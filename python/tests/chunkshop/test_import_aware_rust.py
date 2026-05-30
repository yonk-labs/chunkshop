"""Sub-project C narrowing on a NON-Python language (Rust `use` imports).

Closes the gap that import-aware narrowing was only end-to-end tested with
Python source. Here two Rust files define ``helper``; a third ``use``-imports
one and calls it — the ambiguous match must narrow to the imported module via
the same stem-in-import-tokens heuristic.
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_rust")

from chunkshop.config import CodeRelationshipsExtractor as Cfg
from chunkshop.extractors.code_relationships import CodeRelationshipsExtractor

_RS_DEF = "pub fn helper(v: i32) -> i32 {\n    v * 2\n}\n"


def test_rust_ambiguous_narrows_by_use_import() -> None:
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_RS_DEF, language="rust", source_path="a.rs")
    ext.extract(_RS_DEF, language="rust", source_path="b.rs")
    ext.extract(
        "use crate::a::helper;\n\npub fn run(v: i32) -> i32 {\n    helper(v)\n}\n",
        language="rust",
        source_path="c.rs",
    )
    edges = ext.finalize(project_id="t")
    helper_edges = [
        e for e in edges
        if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")
    ]
    assert len(helper_edges) == 1, f"expected 1 narrowed edge, got {len(helper_edges)}"
    assert helper_edges[0]["dst_fqn"] == "a.helper"
    assert helper_edges[0]["evidence"]["resolution"] == "import_resolved"
    assert helper_edges[0]["provenance"] == "heuristic"


def test_rust_no_use_keeps_fanout() -> None:
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_RS_DEF, language="rust", source_path="a.rs")
    ext.extract(_RS_DEF, language="rust", source_path="b.rs")
    ext.extract(
        "pub fn run(v: i32) -> i32 {\n    helper(v)\n}\n",
        language="rust",
        source_path="c.rs",
    )
    edges = ext.finalize(project_id="t")
    helper_edges = [
        e for e in edges
        if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")
    ]
    assert len(helper_edges) == 2
    assert all(e["evidence"]["resolution"] == "ambiguous_name" for e in helper_edges)
