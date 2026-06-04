# python/src/chunkshop/incremental_cursor.py
"""Atomic JSON persistence for the local ``files`` source's incremental cursor.

The cursor is an opaque ``{path: {"h", "mt", "sz"}}`` dict (see
``chunkshop.sources.files.FilesSource``). Writes go to a temp file in the same
directory, then ``os.replace`` swaps it in — atomic on POSIX, so a crash mid-write
never corrupts an existing cursor. chunkshop only writes the cursor on a fully
successful run (see ``runner.run_cell``); a failed run leaves the prior cursor
untouched and the next run safely re-ingests via idempotent upsert.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def load_cursor(path: str | os.PathLike) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_cursor_atomic(path: str | os.PathLike, cursor: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cursor, f)
    os.replace(tmp, p)  # atomic swap on POSIX
