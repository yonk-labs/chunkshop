# tests/chunkshop/test_example_sync_loop.py
import asyncio, importlib.util, pathlib, pytest
from chunkshop.sources.base import Document, SyncMode

EXAMPLE = pathlib.Path(__file__).parents[2] / "examples" / "sync_loop.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_loop_example", EXAMPLE)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


class _Src:
    sync_mode = SyncMode.CURSOR
    def __init__(self): self._n = 0
    def empty_cursor(self): return {"seq": 0}
    def iter_changes_since(self, cursor):
        if cursor.get("seq", 0) < 1:
            yield Document(id="a", content="hello", fingerprint="fp1")
    def cursor_from(self, last_document): return {"seq": 1}


def test_sync_loop_runs_and_advances_cursor():
    mod = _load_module()
    seen = []
    result = asyncio.run(mod.run_sync(
        sources={"s1": _Src()},
        cursors={"s1": {"seq": 0}},
        on_document=lambda src_name, doc: seen.append((src_name, doc.id)),
        max_concurrent_tasks=2,
    ))
    assert seen == [("s1", "a")]
    assert result["s1"].docs_emitted == 1
    assert result["s1"].new_cursor == {"seq": 1}
    assert result["s1"].success is True


def test_sync_loop_isolates_failures():
    mod = _load_module()
    class _Boom(_Src):
        def iter_changes_since(self, cursor): raise RuntimeError("boom")
    result = asyncio.run(mod.run_sync(
        sources={"ok": _Src(), "bad": _Boom()},
        cursors={"ok": {"seq": 0}, "bad": {"seq": 0}},
        on_document=lambda *a: None, max_concurrent_tasks=2))
    assert result["ok"].success is True
    assert result["bad"].success is False
    assert isinstance(result["bad"].error, RuntimeError)
