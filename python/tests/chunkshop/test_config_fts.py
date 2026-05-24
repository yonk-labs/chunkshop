"""SC-001: FtsConfig model + TargetConfig.fts."""
import pytest
from pydantic import ValidationError
from chunkshop.config import TargetConfig, FtsConfig


def test_fts_defaults_off():
    assert FtsConfig().enabled is False
    assert FtsConfig().language == "english"
    assert FtsConfig().include_metadata_paths == []


def test_target_fts_attaches_and_defaults_none():
    t = TargetConfig(type="postgres", database="db", table="chunks",
                     dsn="postgresql://localhost/test")
    assert t.fts is None
    t2 = TargetConfig(type="postgres", database="db", table="chunks",
                      dsn="postgresql://localhost/test",
                      fts={
                          "enabled": True,
                          "language": "english",
                          "include_metadata_paths": ["lede_report.search_text"],
                      })
    assert t2.fts.enabled is True
    assert t2.fts.include_metadata_paths == ["lede_report.search_text"]


def test_fts_rejects_unknown_language():
    with pytest.raises(ValidationError):
        FtsConfig(enabled=True, language="klingon")


def test_fts_forbids_extra():
    with pytest.raises(ValidationError):
        FtsConfig(enabled=True, languagee="english")


def test_fts_rejects_bad_metadata_path():
    with pytest.raises(ValidationError):
        FtsConfig(enabled=True, include_metadata_paths=["lede_report.search-text"])
