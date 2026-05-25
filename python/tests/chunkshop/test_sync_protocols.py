# tests/chunkshop/test_sync_protocols.py
from chunkshop.sources.base import (
    Document, IncrementalSource, PrunableSource, SyncMode,
)


class _Inc:
    sync_mode = SyncMode.CURSOR

    def empty_cursor(self): return {}

    def iter_changes_since(self, cursor):
        if not cursor:
            yield Document(id="a", content="x")

    def cursor_from(self, last_document): return {"after": last_document.id}


class _Prune:
    def empty_prune_cursor(self): return {}
    def iter_deleted_since(self, cursor): return iter([])


def test_incremental_runtime_checkable():
    assert isinstance(_Inc(), IncrementalSource)
    assert not isinstance(object(), IncrementalSource)


def test_prunable_runtime_checkable():
    assert isinstance(_Prune(), PrunableSource)
    assert not isinstance(object(), PrunableSource)
