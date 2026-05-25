# tests/chunkshop/test_testing_helpers.py
import pytest
from chunkshop.sources.base import Document, SyncMode
from chunkshop.testing import assert_cursor_advances, assert_idempotent_on_re_emit


class _GoodInc:
    sync_mode = SyncMode.CURSOR
    def empty_cursor(self): return {"seq": 0}
    def iter_changes_since(self, cursor):
        if cursor.get("seq", 0) < 1:
            yield Document(id="a", content="x")
    def cursor_from(self, last_document): return {"seq": 1}


class _BadInc(_GoodInc):
    # never advances — always re-emits
    def cursor_from(self, last_document): return {"seq": 0}


def test_assert_cursor_advances_passes_for_good():
    assert_cursor_advances(_GoodInc())


def test_assert_cursor_advances_fails_for_bad():
    with pytest.raises(AssertionError):
        assert_cursor_advances(_BadInc())


def test_idempotent_on_re_emit_passes_for_good():
    assert_idempotent_on_re_emit(_GoodInc())


def test_idempotent_fails_when_re_emits():
    with pytest.raises(AssertionError):
        assert_idempotent_on_re_emit(_BadInc())
