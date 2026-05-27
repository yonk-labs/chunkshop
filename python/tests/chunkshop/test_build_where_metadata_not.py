"""Unit tests for the `metadata_not` WHERE predicate (Phase C, Task C1)."""
from chunkshop.search import _build_where


def test_metadata_not_emits_is_distinct_from():
    sql, params = _build_where({"metadata_not": {"kind": "fact"}})
    assert "IS DISTINCT FROM" in sql
    assert "fact" in params


def test_metadata_not_combines_with_equality():
    sql, params = _build_where({"metadata": {"source": "x"}, "metadata_not": {"kind": "fact"}})
    # metadata_not binds the value as a bare param; the equality block binds the
    # whole filter as a json-encoded `@>` param (so "x" is inside that string).
    assert "fact" in params
    assert any("x" in str(p) for p in params)
    assert "IS DISTINCT FROM" in sql and "@>" in sql


def test_unknown_key_still_rejected():
    import pytest
    with pytest.raises(ValueError):
        _build_where({"bogus": 1})
