import pathlib

import pytest

from chunkshop.sources.derivers import provenance
from chunkshop.sources.derivers.provenance import derivation_hash, stamp_provenance
from chunkshop.sources.parsers.result import ParseResult


def test_hash_is_stable_for_same_inputs():
    a = derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 3})
    b = derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 3})
    assert a == b


def test_hash_ignores_dict_ordering():
    a = derivation_hash("r", "m", "1", {"x": 1, "y": 2})
    b = derivation_hash("r", "m", "1", {"y": 2, "x": 1})
    assert a == b


def test_hash_changes_when_any_input_changes():
    base = derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 3})
    assert derivation_hash("ocr", "clip-ViT-B-32", "1", {"top_k": 3}) != base
    assert derivation_hash("clip_tags", "other", "1", {"top_k": 3}) != base
    assert derivation_hash("clip_tags", "clip-ViT-B-32", "2", {"top_k": 3}) != base
    assert derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 5}) != base


def test_stamp_adds_provenance_and_origin():
    out = stamp_provenance(
        ParseResult(text="a cat", metadata={"width": 8}),
        rung="clip_tags",
        model_id="Qdrant/clip-ViT-B-32-text",
        model_version="1",
        params={"top_k": 3},
    )
    assert out.text == "a cat"
    assert out.metadata["width"] == 8                      # preserved
    assert out.metadata["content_origin"] == "derived"
    assert out.metadata["derived_rung"] == "clip_tags"
    assert out.metadata["derived"]["model"] == "Qdrant/clip-ViT-B-32-text"
    assert out.metadata["derived"]["model_version"] == "1"
    assert out.metadata["derived"]["params_hash"]
    assert out.metadata["derived"]["ts"]


def test_stamp_does_not_mutate_the_input():
    original = ParseResult(text="x", metadata={"a": 1})
    stamp_provenance(original, rung="r", model_id="m", model_version="1", params={})
    assert original.metadata == {"a": 1}


def test_path_valued_param_raises_with_key_name():
    """Path objects are not JSON-serializable; error must name the key."""
    with pytest.raises(TypeError, match="Parameter 'model_path' is not JSON-serializable"):
        derivation_hash("test", "test_model", "1", {"model_path": pathlib.Path("/foo/bar")})


def test_set_valued_param_raises_with_key_name():
    """Set objects are not JSON-serializable; error must name the key."""
    with pytest.raises(TypeError, match="Parameter 'tags' is not JSON-serializable"):
        derivation_hash(
            "test",
            "test_model",
            "1",
            {"tags": {"clip_tags", "ocr_text", "caption_long"}},
        )


def test_cache_schema_version_affects_hash(monkeypatch):
    """Different CACHE_SCHEMA_VERSION values must produce different hashes."""
    base = derivation_hash("r", "m", "1", {"x": 1})
    # Monkeypatch the version constant
    monkeypatch.setattr(provenance, "CACHE_SCHEMA_VERSION", "2")
    modified = derivation_hash("r", "m", "1", {"x": 1})
    assert base != modified
