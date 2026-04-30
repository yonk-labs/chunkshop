"""Tests for the dedup'd warning behavior in chunkers._oversize.

Brief SC-006: when if_oversize is None and an oversize chunk would be emitted,
chunker logs ONE warning per cell instance, not per chunk.
"""
from __future__ import annotations

import logging

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._oversize import DedupedWarner


def test_warner_emits_once_then_silent(caplog):
    caplog.set_level(logging.WARNING, logger="chunkshop")
    w = DedupedWarner(chunker_name="neighbor_expand", ceiling=2000)
    w.warn_once(oversize_len=8000)
    w.warn_once(oversize_len=4500)
    w.warn_once(oversize_len=10000)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "neighbor_expand" in msg
    assert "2000" in msg
    assert "if_oversize" in msg


def test_warner_includes_copy_paste_hint(caplog):
    caplog.set_level(logging.WARNING, logger="chunkshop")
    w = DedupedWarner(chunker_name="summary_embed", ceiling=1500)
    w.warn_once(oversize_len=3000)
    msg = caplog.records[0].getMessage()
    assert "if_oversize:" in msg
