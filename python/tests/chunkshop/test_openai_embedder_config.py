"""Config validation for the openai remote embedder."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from chunkshop.config import OpenAIEmbedder


def test_openai_embedder_minimal_defaults() -> None:
    e = OpenAIEmbedder(type="openai", model="text-embedding-3-small", dim=1536)
    assert e.base_url == "https://api.openai.com/v1"
    assert e.api_key_env is None
    assert e.batch_size == 64 and e.max_retries == 3 and e.timeout == 60.0


def test_openai_embedder_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbedder(type="openai", model="m", dim=8, endpoint="x")  # type: ignore[call-arg]


def test_openai_embedder_requires_http_base_url() -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbedder(type="openai", model="m", dim=8, base_url="ftp://nope")


def test_openai_embedder_rejects_nonpositive_dim() -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbedder(type="openai", model="m", dim=0)


def test_embedder_union_dispatches_on_type() -> None:
    """The EmbedderConfig discriminated union routes `type: openai` to
    OpenAIEmbedder (and still routes `type: fastembed` to FastembedEmbedder)."""
    from pydantic import TypeAdapter

    from chunkshop.config import EmbedderConfig, FastembedEmbedder

    adapter = TypeAdapter(EmbedderConfig)
    openai = adapter.validate_python(
        {
            "type": "openai",
            "model": "voyage-3",
            "dim": 1024,
            "base_url": "https://api.voyageai.com/v1",
            "api_key_env": "VOYAGE_API_KEY",
        }
    )
    assert isinstance(openai, OpenAIEmbedder)
    assert openai.model == "voyage-3"

    fast = adapter.validate_python(
        {"type": "fastembed", "model_name": "x", "dim": 8}
    )
    assert isinstance(fast, FastembedEmbedder)
