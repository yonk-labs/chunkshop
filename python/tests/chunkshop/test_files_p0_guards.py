"""P0 guards: files incremental vs overwrite (PYD-01), duplicate stem ids (PYS-01)."""
import pytest
from pydantic import ValidationError

from chunkshop.config import CellConfig, FilesSource as Cfg
from chunkshop.sources.files import FilesSource as Adapter


def _cell(tmp_path, *, mode=None, incremental=True):
    src = {"type": "files", "glob": str(tmp_path / "*.md")}
    if incremental:
        src["incremental"] = {"cursor_path": str(tmp_path / "cur.json")}
    target = {"type": "sqlite", "dsn": str(tmp_path / "v.db"), "database": "main", "table": "t"}
    if mode:
        target["mode"] = mode
    return {
        "cell_name": "c", "source": src,
        "chunker": {"type": "sentence_aware"},
        "embedder": {"type": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dim": 384},
        "target": target,
    }


@pytest.mark.parametrize("mode", [None, "overwrite"])
def test_incremental_with_overwrite_rejected(tmp_path, mode):
    with pytest.raises(ValidationError) as ei:
        CellConfig(**_cell(tmp_path, mode=mode))
    msg = str(ei.value)
    assert "incremental" in msg and "overwrite" in msg and "create_if_missing" in msg


def test_incremental_with_create_if_missing_loads(tmp_path):
    CellConfig(**_cell(tmp_path, mode="create_if_missing"))


def test_overwrite_without_incremental_still_loads(tmp_path):
    CellConfig(**_cell(tmp_path, incremental=False))

