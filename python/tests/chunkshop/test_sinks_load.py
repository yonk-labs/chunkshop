import os
import pytest
from unittest.mock import MagicMock

from chunkshop.sinks import load_sink


def test_load_sink_postgres(monkeypatch):
    monkeypatch.setenv("DUMMY_DSN", "postgresql://x:1/y")
    cfg = MagicMock(type="postgres", dsn_env="DUMMY_DSN")
    cfg.database_name = "x"
    cfg.table = "y"
    sink = load_sink(cfg, embed_dim=384)
    assert sink.__class__.__name__ == "PgSink"


def test_load_sink_unknown():
    cfg = MagicMock(type="oracle", dsn_env="X")
    with pytest.raises(ValueError, match="unknown target"):
        load_sink(cfg, embed_dim=384)
