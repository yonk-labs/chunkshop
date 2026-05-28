"""Pure-Python sanity tests for the CS-5 Provenance ontology.

No PG, no fixtures — just the type alias and tuple imported straight
from chunkshop.extractors.code_relationships.
"""
from __future__ import annotations


def test_provenances_tuple_is_three_value_set() -> None:
    """PROVENANCES must be exactly ('ast', 'scip', 'heuristic') in that order."""
    from chunkshop.extractors.code_relationships import PROVENANCES

    assert PROVENANCES == ("ast", "scip", "heuristic")
    assert len(PROVENANCES) == 3
    assert all(p.islower() and p.isalpha() for p in PROVENANCES)
    assert len(set(PROVENANCES)) == 3


def test_provenance_literal_is_importable() -> None:
    """Provenance is a Literal[...] that mypy/pyright can narrow."""
    from chunkshop.extractors.code_relationships import Provenance  # noqa: F401

    from typing import get_args
    assert set(get_args(Provenance)) == {"ast", "scip", "heuristic"}
