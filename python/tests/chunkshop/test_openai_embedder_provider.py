"""Unit tests for OpenAIEmbeddingProvider (mocked HTTP)."""
from __future__ import annotations

import io
import json

import numpy as np
import pytest

from chunkshop.config import OpenAIEmbedder
from chunkshop.embedders.openai_provider import OpenAIEmbeddingProvider


class _FakeResp:
    """Minimal context-manager stand-in for urlopen()'s return."""

    def __init__(self, payload: dict):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _embeddings_payload(vectors, start_index=0):
    return {
        "data": [
            {"index": start_index + i, "embedding": v} for i, v in enumerate(vectors)
        ]
    }


def _cfg(**kw):
    base = dict(type="openai", model="m", dim=3, base_url="https://api.test/v1")
    base.update(kw)
    return OpenAIEmbedder(**base)


def test_embed_posts_and_returns_float32(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResp(_embeddings_payload([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]))

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", fake_urlopen
    )
    p = OpenAIEmbeddingProvider(_cfg())
    out = p.embed(["a", "b"])

    assert out.dtype == np.float32 and out.shape == (2, 3)
    assert captured["url"] == "https://api.test/v1/embeddings"
    assert captured["body"]["model"] == "m"
    assert captured["body"]["input"] == ["a", "b"]
    assert captured["body"]["encoding_format"] == "float"
    assert "authorization" not in captured["headers"]  # keyless
    assert p.embed_seconds >= 0.0


def test_embed_sends_bearer_when_api_key_env_set(monkeypatch):
    monkeypatch.setenv("MY_EMB_KEY", "secret-xyz")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResp(_embeddings_payload([[1.0, 2.0, 3.0]]))

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", fake_urlopen
    )
    p = OpenAIEmbeddingProvider(_cfg(api_key_env="MY_EMB_KEY"))
    p.embed(["a"])
    assert captured["headers"]["authorization"] == "Bearer secret-xyz"


def test_missing_api_key_env_raises():
    with pytest.raises(ValueError, match="unset or empty"):
        OpenAIEmbeddingProvider(_cfg(api_key_env="DEFINITELY_NOT_SET_12345"))


def test_response_sorted_by_index(monkeypatch):
    payload = {
        "data": [
            {"index": 1, "embedding": [9.0, 9.0, 9.0]},
            {"index": 0, "embedding": [1.0, 1.0, 1.0]},
        ]
    }
    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(payload),
    )
    out = OpenAIEmbeddingProvider(_cfg()).embed(["first", "second"])
    assert out[0].tolist() == [1.0, 1.0, 1.0]
    assert out[1].tolist() == [9.0, 9.0, 9.0]


def test_dim_mismatch_raises(monkeypatch):
    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResp(_embeddings_payload([[1.0, 2.0]])),  # dim 2
    )
    with pytest.raises(ValueError, match="dim"):
        OpenAIEmbeddingProvider(_cfg()).embed(["a"])  # cfg says dim=3


def test_empty_input_returns_zero_rows():
    out = OpenAIEmbeddingProvider(_cfg()).embed([])
    assert out.shape == (0, 3) and out.dtype == np.float32


def test_batching_splits_requests(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        batch = json.loads(req.data.decode())["input"]
        calls.append(len(batch))
        return _FakeResp(_embeddings_payload([[1.0, 2.0, 3.0]] * len(batch)))

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", fake_urlopen
    )
    p = OpenAIEmbeddingProvider(_cfg(batch_size=2))
    out = p.embed(["a", "b", "c", "d", "e"])
    assert out.shape == (5, 3)
    assert calls == [2, 2, 1]  # 5 inputs at batch_size=2
