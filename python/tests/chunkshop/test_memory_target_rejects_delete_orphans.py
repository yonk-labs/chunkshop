"""delete_orphans is unsupported for memory targets: MemorySink offsets seq_nums per session (slice 4)."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import chunkshop
from chunkshop.config import CellConfig, TargetConfig, load_config

PRESETS = Path(chunkshop.__file__).parent / "configs" / "memory"


def _target(**extra):
    return {"type": "sqlite", "dsn": "x.db", "database": "main", "table": "t", **extra}


@pytest.mark.parametrize("tier", ["provisional", "consolidated"])
def test_memory_with_delete_orphans_fails_to_load(tier):
    with pytest.raises(ValidationError, match="delete_orphans is not supported for memory targets"):
        TargetConfig(**_target(memory={"tier": tier}, delete_orphans=True))


def test_memory_without_delete_orphans_and_delete_orphans_without_memory_still_load():
    TargetConfig(**_target(memory={"tier": "provisional"}))
    TargetConfig(**_target(delete_orphans=True))


@pytest.mark.parametrize("name", ["consolidate.yaml", "realtime.yaml"])
def test_shipped_memory_presets_still_load(name):
    assert isinstance(load_config(PRESETS / name), CellConfig)
