# tests/chunkshop/test_sync_settings_config.py
import pytest
from pydantic import ValidationError
from chunkshop.config import SyncSettings


def test_defaults():
    s = SyncSettings()
    assert s.mode == "full_resync"
    assert s.refresh_freq_seconds is None
    assert s.prune_freq_seconds is None


def test_mode_validated():
    s = SyncSettings(mode="cursor", refresh_freq_seconds=3600, prune_freq_seconds=86400)
    assert s.mode == "cursor"
    assert s.refresh_freq_seconds == 3600


def test_rejects_unknown_field():
    with pytest.raises(ValidationError):
        SyncSettings(mode="cursor", bogus=1)
