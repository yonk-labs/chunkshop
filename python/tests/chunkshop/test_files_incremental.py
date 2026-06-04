# python/tests/chunkshop/test_files_incremental.py
import hashlib
import os
from pathlib import Path

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.base import IncrementalSource, SyncMode
from chunkshop.testing import (
    assert_cursor_advances, assert_idempotent_on_re_emit, merge_cursor,
)


def _cfg(tmp_path, detect="hash"):
    return Cfg(
        type="files", glob=str(tmp_path / "**" / "*.md"), id_from="path",
        incremental={"cursor_path": str(tmp_path / "cur.json"), "detect": detect},
    )


def test_files_is_incremental(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    src = FilesSource(_cfg(tmp_path))
    assert isinstance(src, IncrementalSource)
    assert src.sync_mode == SyncMode.CURSOR


def test_full_resync_from_empty_cursor_sets_fingerprint(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    src = FilesSource(_cfg(tmp_path))
    docs = sorted(src.iter_changes_since(src.empty_cursor()), key=lambda d: d.id)
    assert [d.id for d in docs] == [str(tmp_path / "a.md"), str(tmp_path / "b.md")]
    assert docs[0].fingerprint == hashlib.sha256(b"alpha").hexdigest()


def test_exact_delta_after_edit_and_add(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    src = FilesSource(_cfg(tmp_path))
    cursor = src.empty_cursor()
    first = list(src.iter_changes_since(cursor))
    cursor = merge_cursor(src, cursor, first)
    assert list(src.iter_changes_since(cursor)) == []
    (tmp_path / "a.md").write_text("alpha-v2")
    (tmp_path / "c.md").write_text("gamma")
    changed = list(src.iter_changes_since(cursor))
    assert {d.id for d in changed} == {str(tmp_path / "a.md"), str(tmp_path / "c.md")}


def test_mtime_mode_differs_from_hash_mode(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("alpha")
    st = f.stat()
    src_m = FilesSource(_cfg(tmp_path, detect="mtime"))
    cursor = merge_cursor(src_m, src_m.empty_cursor(),
                          list(src_m.iter_changes_since(src_m.empty_cursor())))
    f.write_text("ALPHA")  # same byte length (5), different content
    os.utime(f, (st.st_atime, st.st_mtime))  # pin mtime back
    assert list(src_m.iter_changes_since(cursor)) == [], "mtime mode must skip pinned file"
    src_h = FilesSource(_cfg(tmp_path, detect="hash"))
    cursor_h = merge_cursor(src_h, src_h.empty_cursor(),
                            list(src_h.iter_changes_since(src_h.empty_cursor())))
    f.write_text("OMEGA")  # length 5 again
    os.utime(f, (st.st_atime, st.st_mtime))
    assert [d.id for d in src_h.iter_changes_since(cursor_h)] == [str(f)], \
        "hash mode must catch a content change even with pinned mtime"


def test_passes_shared_incremental_contracts(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    src = FilesSource(_cfg(tmp_path))
    assert_cursor_advances(src)
    assert_idempotent_on_re_emit(src)
