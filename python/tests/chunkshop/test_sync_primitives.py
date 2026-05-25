# tests/chunkshop/test_sync_primitives.py
from chunkshop.sources.base import Document, SyncMode, StaleCursorError


def test_syncmode_values():
    assert SyncMode.FULL_RESYNC == "full_resync"
    assert SyncMode.CURSOR == "cursor"
    assert SyncMode.FINGERPRINT == "fingerprint"


def test_document_fingerprint_optional_default_none():
    d = Document(id="a", content="x")
    assert d.fingerprint is None
    d2 = Document(id="a", content="x", fingerprint="etag-123")
    assert d2.fingerprint == "etag-123"


def test_stale_cursor_error_is_exception():
    with __import__("pytest").raises(StaleCursorError):
        raise StaleCursorError("cursor expired")
