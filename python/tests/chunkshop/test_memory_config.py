"""Unit tests for memory-primitive pydantic config models."""
import pytest
from pydantic import ValidationError
from chunkshop.config import CellConfig


def _min_cell(**target_over):
    return {
        "cell_name": "m",
        "source": {"type": "session_staging", "dsn": "postgresql://x/y",
                   "staging_table": "chunkshop_staging", "mode": "realtime"},
        "chunker": {"type": "fixed_overlap", "window_words": 200, "step_words": 20},
        "embedder": {"type": "fastembed", "model_name": "Xenova/bge-small-en-v1.5-int8", "dim": 384},
        "target": {"type": "postgres", "dsn": "postgresql://x/y",
                   "database": "mem", "table": "t", "mode": "create_if_missing", **target_over},
    }


def test_session_staging_source_parses():
    c = CellConfig(**_min_cell())
    assert c.source.type == "session_staging"
    assert c.source.mode == "realtime"
    assert c.source.min_age_seconds == 3600
    assert c.source.staging_schema == "public"


def test_session_staging_rejects_bad_table_ident():
    cell = _min_cell()
    cell["source"]["staging_table"] = "bad-name;DROP"
    with pytest.raises(ValidationError, match="staging_table"):
        CellConfig(**cell)


def test_session_staging_rejects_unknown_mode():
    cell = _min_cell()
    cell["source"]["mode"] = "whenever"
    with pytest.raises(ValidationError):
        CellConfig(**cell)


def test_session_episode_framer_defaults():
    cell = _min_cell()
    cell["framer"] = {"type": "session_episode"}
    c = CellConfig(**cell)
    assert c.framer.type == "session_episode"
    assert c.framer.max_gap_seconds == 1800
    assert c.framer.max_turns == 40
    assert c.framer.boundary_on_tool is True


def test_memory_config_numeric_lower_bounds():
    cell = _min_cell()
    cell["framer"] = {"type": "session_episode", "max_turns": 0}
    with pytest.raises(ValidationError):
        CellConfig(**cell)
    cell2 = _min_cell()
    cell2["source"]["min_age_seconds"] = -1
    with pytest.raises(ValidationError):
        CellConfig(**cell2)
    # min_age_seconds == 0 is allowed
    cell3 = _min_cell()
    cell3["source"]["min_age_seconds"] = 0
    assert CellConfig(**cell3).source.min_age_seconds == 0
