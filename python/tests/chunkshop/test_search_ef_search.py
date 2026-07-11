"""#68: pgvector HNSW ef_search query knob — validation + end-to-end threading.

Hermetic: monkeypatches the DB executor so no Postgres is required.
"""
import numpy as np
import pytest

from chunkshop import search as search_mod


def test_validate_ef_search_bounds_and_types():
    assert search_mod._validate_ef_search(None) is None
    assert search_mod._validate_ef_search(1) == 1
    assert search_mod._validate_ef_search(1000) == 1000
    # bool is an int subclass — must be rejected, not silently treated as 1.
    for bad in (0, 1001, -5, True, 3.5, "100"):
        with pytest.raises(ValueError):
            search_mod._validate_ef_search(bad)


def test_semantic_search_threads_ef_search_to_executor(monkeypatch):
    captured = {}

    def fake_exec(dsn, sql, params, *, ef_search=None):
        captured["ef_search"] = ef_search
        return []

    monkeypatch.setattr(search_mod, "_execute_read", fake_exec)
    search_mod.semantic_search(
        "postgresql://x",
        schema="s",
        table="t",
        query_vec=np.zeros(4, dtype=np.float32),
        k=5,
        ef_search=128,
    )
    assert captured["ef_search"] == 128


def test_semantic_search_rejects_bad_ef_search_before_db(monkeypatch):
    def boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("DB path hit despite invalid ef_search")

    monkeypatch.setattr(search_mod, "_execute_read", boom)
    with pytest.raises(ValueError):
        search_mod.semantic_search(
            "postgresql://x",
            schema="s",
            table="t",
            query_vec=np.zeros(4, dtype=np.float32),
            k=5,
            ef_search=99999,
        )
