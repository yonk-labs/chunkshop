"""Tests for the recursion guard in apply_if_oversize.

Brief SC-008: if_oversize chains can themselves contain if_oversize, but the
runner stops descending after 5 levels and raises OversizeRecursionError.
"""
from __future__ import annotations

import pytest

from chunkshop.chunkers._oversize import OversizeRecursionError, apply_if_oversize
from chunkshop.chunkers.base import Chunk
from chunkshop.sources.base import Document
from chunkshop.config import FixedOverlapChunker as FixedCfg
from chunkshop.chunkers.fixed_overlap import FixedOverlapChunker


def _doc(text: str) -> Document:
    return Document(id="doc1", content=text, metadata={})


def _oversize_chunk(text: str) -> Chunk:
    return Chunk(
        doc_id="doc1",
        seq_num=0,
        original_content=text,
        embedded_content=text,
        metadata={},
    )


def _build_chunker_factory():
    """Returns a build_chunker callable used by apply_if_oversize."""
    from chunkshop.chunkers import load_chunker
    return lambda cfg: load_chunker(cfg)


def test_recursion_depth_5_succeeds():
    """A chain of 5 nested if_oversize chunkers terminates without raising.

    Each layer tightens window_words, so output chunks shrink across the chain.
    SC-008 only requires depth 5 succeeds (does not raise OversizeRecursionError);
    we assert that contract, not a specific terminal size.
    """
    # Build chain: layer5 (innermost) → layer4 → ... → layer1 (outermost call).
    # Each tighter than the last so progress is real.
    layer5 = FixedCfg(type="fixed_overlap", window_words=2, step_words=2, max_chars=15)
    layer4 = FixedCfg(type="fixed_overlap", window_words=4, step_words=4, max_chars=25, if_oversize=layer5)
    layer3 = FixedCfg(type="fixed_overlap", window_words=8, step_words=8, max_chars=50, if_oversize=layer4)
    layer2 = FixedCfg(type="fixed_overlap", window_words=16, step_words=16, max_chars=100, if_oversize=layer3)
    layer1 = FixedCfg(type="fixed_overlap", window_words=32, step_words=32, max_chars=200, if_oversize=layer2)

    chunk = _oversize_chunk("word " * 1000)  # ~5000 chars
    # Should NOT raise — recursion terminates within depth 5.
    out = apply_if_oversize(
        [chunk],
        ceiling=200,
        if_oversize_cfg=layer1,
        chunker_name="test",
        build_chunker=_build_chunker_factory(),
        document=_doc("word " * 1000),
        depth=0,
    )
    assert len(out) >= 1  # recursion produced output


def test_recursion_depth_6_raises():
    inner = FixedCfg(type="fixed_overlap", window_words=2, step_words=2, max_chars=10)
    layer5 = FixedCfg(type="fixed_overlap", window_words=2, step_words=2, max_chars=10, if_oversize=inner)
    layer4 = FixedCfg(type="fixed_overlap", window_words=2, step_words=2, max_chars=10, if_oversize=layer5)
    layer3 = FixedCfg(type="fixed_overlap", window_words=2, step_words=2, max_chars=10, if_oversize=layer4)
    layer2 = FixedCfg(type="fixed_overlap", window_words=2, step_words=2, max_chars=10, if_oversize=layer3)
    layer1 = FixedCfg(type="fixed_overlap", window_words=2, step_words=2, max_chars=10, if_oversize=layer2)

    chunk = _oversize_chunk("a" * 100)
    with pytest.raises(OversizeRecursionError, match="depth"):
        apply_if_oversize(
            [chunk],
            ceiling=10,
            if_oversize_cfg=layer1,
            chunker_name="test",
            build_chunker=_build_chunker_factory(),
            document=_doc("a" * 100),
            depth=0,
        )
