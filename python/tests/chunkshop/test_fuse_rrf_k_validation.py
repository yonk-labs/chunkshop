"""Regression: hybrid RRF fusion must reject a non-positive ``rrf_k``.

Found via pre-release search stress-testing: ``hybrid_search(rrf_k=-1)`` divided
by zero in ``_fuse_rrf`` (``1.0 / (rrf_k + rank)`` with ``rank == -rrf_k``),
surfacing an opaque ``ZeroDivisionError`` instead of a clean ``ValueError``.
``rrf_k`` is a public parameter with no lower-bound validation. RRF's constant
must be positive (canonical value 60).

These tests are pure (no DB) — they exercise ``fuse`` directly.
"""
from __future__ import annotations

import pytest

from chunkshop.search_common import Hit, fuse


def _legs() -> dict[str, list[Hit]]:
    hit = Hit(
        doc_id="d1",
        seq_num=0,
        text="hello",
        score=1.0,
        metadata={},
        legs=("semantic",),
    )
    return {"semantic": [hit]}


def test_rrf_k_negative_raises_clean_valueerror():
    with pytest.raises(ValueError, match="rrf_k"):
        fuse(_legs(), k=10, fusion="rrf", weights=None, rrf_k=-1)


def test_rrf_k_zero_raises_clean_valueerror():
    with pytest.raises(ValueError, match="rrf_k"):
        fuse(_legs(), k=10, fusion="rrf", weights=None, rrf_k=0)


def test_rrf_k_positive_still_works():
    out = fuse(_legs(), k=10, fusion="rrf", weights=None, rrf_k=60)
    assert len(out) == 1
    assert out[0].doc_id == "d1"


def test_bad_rrf_k_ignored_for_weighted_fusion():
    # weighted fusion never reads rrf_k, so a garbage value must not be rejected.
    out = fuse(_legs(), k=10, fusion="weighted", weights={"semantic": 1.0}, rrf_k=-1)
    assert len(out) == 1
