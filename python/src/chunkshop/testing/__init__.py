# src/chunkshop/testing/__init__.py
"""Reusable connector test helpers. Importable by chunkshop's own tests and by
downstream plugins to validate their IncrementalSource implementations."""
from __future__ import annotations
from chunkshop.sources.base import IncrementalSource


def assert_cursor_advances(source: IncrementalSource) -> None:
    """Run a full cycle and assert the cursor moves off empty after ingesting."""
    cursor = source.empty_cursor()
    docs = list(source.iter_changes_since(cursor))
    assert docs, "expected at least one document on first sync"
    new_cursor = source.cursor_from(docs[-1])
    assert new_cursor != cursor, (
        f"cursor did not advance: {cursor!r} == {new_cursor!r}")


def assert_idempotent_on_re_emit(source: IncrementalSource) -> None:
    """First sync yields docs; re-syncing from the advanced cursor yields none."""
    cursor = source.empty_cursor()
    docs = list(source.iter_changes_since(cursor))
    assert docs, "expected documents on first sync"
    advanced = source.cursor_from(docs[-1])
    again = list(source.iter_changes_since(advanced))
    assert not again, f"expected no re-emit after cursor advance, got {len(again)} docs"
