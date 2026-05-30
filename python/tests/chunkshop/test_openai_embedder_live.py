"""Live smoke test against a real OpenAI-compatible endpoint. Skips unless
CHUNKSHOP_TEST_OPENAI_BASE_URL is set. Point it at a local Ollama/TEI:

    CHUNKSHOP_TEST_OPENAI_BASE_URL=http://localhost:11434/v1 \\
    CHUNKSHOP_TEST_OPENAI_MODEL=nomic-embed-text \\
    CHUNKSHOP_TEST_OPENAI_DIM=768 \\
      uv run --no-sync pytest tests/chunkshop/test_openai_embedder_live.py -v
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from chunkshop.config import OpenAIEmbedder
from chunkshop.embedders.openai_provider import OpenAIEmbeddingProvider

_BASE = os.environ.get("CHUNKSHOP_TEST_OPENAI_BASE_URL")


@pytest.mark.skipif(not _BASE, reason="set CHUNKSHOP_TEST_OPENAI_BASE_URL to run")
def test_live_embed_shape() -> None:
    dim = int(os.environ["CHUNKSHOP_TEST_OPENAI_DIM"])
    cfg = OpenAIEmbedder(
        type="openai",
        model=os.environ["CHUNKSHOP_TEST_OPENAI_MODEL"],
        dim=dim,
        base_url=_BASE,
        api_key_env=os.environ.get("CHUNKSHOP_TEST_OPENAI_KEY_ENV"),
    )
    out = OpenAIEmbeddingProvider(cfg).embed(["hello world", "second string"])
    assert out.shape == (2, dim) and out.dtype == np.float32
