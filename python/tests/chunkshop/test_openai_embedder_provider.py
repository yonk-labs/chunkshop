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


import urllib.error


def _http_error(code):
    return urllib.error.HTTPError(
        url="https://api.test/v1/embeddings", code=code, msg="x", hdrs=None,
        fp=io.BytesIO(b'{"error":"boom"}'),
    )


def test_retries_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.time.sleep", lambda *_: None
    )
    seq = [_http_error(503), _FakeResp(_embeddings_payload([[1.0, 2.0, 3.0]]))]

    def fake_urlopen(req, timeout=None):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", fake_urlopen
    )
    out = OpenAIEmbeddingProvider(_cfg(max_retries=2)).embed(["a"])
    assert out.shape == (1, 3) and not seq  # both queue items consumed


def test_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.time.sleep", lambda *_: None
    )

    def always_503(req, timeout=None):
        raise _http_error(503)

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", always_503
    )
    with pytest.raises(RuntimeError, match="HTTP 503"):
        OpenAIEmbeddingProvider(_cfg(max_retries=1)).embed(["a"])


def test_non_retryable_4xx_raises_immediately(monkeypatch):
    calls = {"n": 0}

    def bad_request(req, timeout=None):
        calls["n"] += 1
        raise _http_error(400)

    monkeypatch.setattr(
        "chunkshop.embedders.openai_provider.urllib.request.urlopen", bad_request
    )
    with pytest.raises(RuntimeError, match="HTTP 400"):
        OpenAIEmbeddingProvider(_cfg(max_retries=3)).embed(["a"])
    assert calls["n"] == 1  # no retries on 400


def test_load_embedder_dispatches_to_openai_provider():
    from chunkshop.embedders import load_embedder
    p = load_embedder(_cfg())
    assert type(p).__name__ == "OpenAIEmbeddingProvider"
    assert p.dim == 3
