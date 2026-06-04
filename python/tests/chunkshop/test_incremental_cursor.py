from pathlib import Path
from chunkshop.incremental_cursor import load_cursor, save_cursor_atomic


def test_load_missing_returns_empty(tmp_path):
    assert load_cursor(tmp_path / "nope.json") == {}


def test_save_then_load_round_trip(tmp_path):
    p = tmp_path / "sub" / "cur.json"  # parent does not exist yet
    cursor = {"a.md": {"h": "deadbeef", "mt": 1.0, "sz": 5}}
    save_cursor_atomic(p, cursor)
    assert p.exists()
    assert load_cursor(p) == cursor


def test_save_leaves_no_tmp_file(tmp_path):
    p = tmp_path / "cur.json"
    save_cursor_atomic(p, {"x": 1})
    assert list(tmp_path.glob("*.tmp")) == []


def test_existing_cursor_intact_until_replace(tmp_path):
    p = tmp_path / "cur.json"
    save_cursor_atomic(p, {"v": 1})
    assert load_cursor(p) == {"v": 1}
    save_cursor_atomic(p, {"v": 2})
    assert load_cursor(p) == {"v": 2}
