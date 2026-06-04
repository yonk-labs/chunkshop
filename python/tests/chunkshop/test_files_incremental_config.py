# python/tests/chunkshop/test_files_incremental_config.py
import pytest
from pydantic import ValidationError
from chunkshop.config import FilesSource


def test_files_incremental_absent_defaults_none():
    cfg = FilesSource(type="files", glob="x/*.md")
    assert cfg.incremental is None


def test_files_incremental_parses_with_defaults():
    cfg = FilesSource(
        type="files", glob="x/*.md",
        incremental={"cursor_path": ".chunkshop/cur.json"},
    )
    assert cfg.incremental.cursor_path == ".chunkshop/cur.json"
    assert cfg.incremental.detect == "hash"  # default


def test_files_incremental_detect_mtime():
    cfg = FilesSource(
        type="files", glob="x/*.md",
        incremental={"cursor_path": "c.json", "detect": "mtime"},
    )
    assert cfg.incremental.detect == "mtime"


def test_files_incremental_rejects_unknown_key():
    with pytest.raises(ValidationError):
        FilesSource(
            type="files", glob="x/*.md",
            incremental={"cursor_path": "c.json", "typo": 1},
        )


def test_files_incremental_rejects_bad_detect():
    with pytest.raises(ValidationError):
        FilesSource(
            type="files", glob="x/*.md",
            incremental={"cursor_path": "c.json", "detect": "git"},
        )
