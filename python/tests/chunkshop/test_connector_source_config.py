# tests/chunkshop/test_connector_source_config.py
import pytest
from pydantic import TypeAdapter, ValidationError
from chunkshop.config import SourceConfig, ConnectorSource


def _parse(d):
    return TypeAdapter(SourceConfig).validate_python(d)


def test_connector_source_parses_via_union():
    cfg = _parse({"type": "connector", "connector": "gdrive",
                  "config": {"folder_id": "abc"},
                  "sync": {"mode": "cursor", "refresh_freq_seconds": 3600}})
    assert isinstance(cfg, ConnectorSource)
    assert cfg.connector == "gdrive"
    assert cfg.config["folder_id"] == "abc"
    assert cfg.sync.mode == "cursor"


def test_connector_config_blob_is_open():
    # config is intentionally an open dict — the plugin validates it.
    cfg = _parse({"type": "connector", "connector": "x", "config": {"any": 1, "thing": [2]}})
    assert cfg.config["thing"] == [2]


def test_connector_top_level_still_forbids_extra():
    with pytest.raises(ValidationError):
        _parse({"type": "connector", "connector": "x", "bogus_top_level": 1})
