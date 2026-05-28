"""Pure-Python sanity tests for the CS-2 EdgeKind ontology.

No PG, no fixtures — just the constants and mapping helper imported
straight from chunkshop.extractors.code_relationships.
"""
from __future__ import annotations

import pytest


def test_edge_kinds_tuple_is_codegraph_canonical_set() -> None:
    """EDGE_KINDS must be the exact 12-value codegraph ontology, in canonical order."""
    from chunkshop.extractors.code_relationships import EDGE_KINDS

    assert EDGE_KINDS == (
        "contains", "calls", "imports", "exports",
        "extends", "implements", "references",
        "type_of", "returns", "instantiates",
        "overrides", "decorates",
    )
    assert len(EDGE_KINDS) == 12
    # All lowercase, snake_case, no duplicates.
    assert all(k.islower() and k.replace("_", "").isalpha() for k in EDGE_KINDS)
    assert len(set(EDGE_KINDS)) == 12


def test_edge_kind_literal_is_importable() -> None:
    """EdgeKind is a Literal[...] that mypy/pyright can narrow."""
    from chunkshop.extractors.code_relationships import EdgeKind  # noqa: F401

    # Literal types have no runtime API beyond __args__.
    from typing import get_args
    assert set(get_args(EdgeKind)) == {
        "contains", "calls", "imports", "exports",
        "extends", "implements", "references",
        "type_of", "returns", "instantiates",
        "overrides", "decorates",
    }


@pytest.mark.parametrize(
    ("legacy", "kind"),
    [
        ("CALLS", "calls"),
        ("INHERITS", "extends"),
        ("IMPLEMENTS", "implements"),
    ],
)
def test_edge_type_to_kind_mapping(legacy: str, kind: str) -> None:
    """The 3 existing uppercase edge_type values map to their codegraph equivalents."""
    from chunkshop.extractors.code_relationships import edge_type_to_kind

    assert edge_type_to_kind(legacy) == kind


def test_edge_type_to_kind_rejects_unknown_value() -> None:
    """Unknown edge_type → explicit error, not silent default."""
    from chunkshop.extractors.code_relationships import edge_type_to_kind

    with pytest.raises(ValueError, match="unknown edge_type"):
        edge_type_to_kind("BOGUS")
