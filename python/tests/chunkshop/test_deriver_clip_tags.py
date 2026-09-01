import pytest

from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

VOCAB = [
    "a solid red square",
    "a solid blue square",
    "a photograph of a cat",
]


@pytest.fixture
def images(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (224, 224), (220, 30, 30)).save(red)
    Image.new("RGB", (224, 224), (30, 30, 220)).save(blue)
    return red, blue


def test_requires_a_non_empty_vocabulary():
    with pytest.raises(ValueError, match="vocabulary"):
        ClipTagsDeriver(vocabulary=[])


@pytest.mark.slow
def test_tags_are_text_and_correct(images):
    """Downloads ~0.6GB of ONNX weights on first run; marked slow."""
    pytest.importorskip("fastembed")
    red, blue = images
    d = ClipTagsDeriver(vocabulary=VOCAB, top_k=1)

    red_result = d.derive(red)
    assert red_result.metadata["clip_tags"][0] == "a solid red square"
    assert "red" in red_result.text.lower()

    blue_result = d.derive(blue)
    assert blue_result.metadata["clip_tags"][0] == "a solid blue square"


@pytest.mark.slow
def test_provenance_names_the_models(images):
    pytest.importorskip("fastembed")
    md = ClipTagsDeriver(vocabulary=VOCAB).derive(images[0]).metadata
    assert md["content_origin"] == "derived"
    assert md["derived_rung"] == "clip_tags"
    assert "clip-ViT-B-32" in md["derived"]["model"]


@pytest.mark.slow
def test_includes_rung_0_metadata(images):
    """clip_tags is additive over image_meta, not a replacement."""
    pytest.importorskip("fastembed")
    md = ClipTagsDeriver(vocabulary=VOCAB).derive(images[0]).metadata
    assert md["width"] == 224
    assert md["image_format"] == "PNG"


class _FakeVision:
    """Stands in for fastembed's ImageEmbedding — no download, no slow mark."""

    def embed(self, paths):
        import numpy as np

        for _ in paths:
            yield np.array([1.0, 0.0, 0.0])


def _fake_deriver(images):
    """A ClipTagsDeriver with model loading bypassed entirely."""
    import numpy as np

    d = ClipTagsDeriver(vocabulary=VOCAB, top_k=1)
    d._ensure_models = lambda: None  # instance override: never touches fastembed
    d._vision = _FakeVision()
    d._label_vectors = np.eye(len(VOCAB))
    return d


def test_derivation_hash_changes_when_fastembed_version_changes(images, monkeypatch):
    """A fastembed upgrade must not serve stale cached tags forever."""
    red, _blue = images
    d = _fake_deriver(images)

    monkeypatch.setattr(ClipTagsDeriver, "_model_version", staticmethod(lambda: "1.2.3"))
    hash_a = d.derivation_hash()

    monkeypatch.setattr(ClipTagsDeriver, "_model_version", staticmethod(lambda: "9.9.9"))
    hash_b = d.derivation_hash()

    assert hash_a != hash_b, "derivation_hash must change when fastembed's version changes"


def test_derivation_hash_matches_what_stamp_provenance_records(images, monkeypatch):
    """Key/record agreement: derivation_hash() and stamp_provenance() must
    use the exact same model_version — this is load-bearing for the cache
    (the cache key IS the provenance record, per provenance.py's docstring).
    """
    from chunkshop.sources.derivers.provenance import derivation_hash as _dhash

    red, _blue = images
    d = _fake_deriver(images)
    monkeypatch.setattr(ClipTagsDeriver, "_model_version", staticmethod(lambda: "1.2.3"))

    hash_ = d.derivation_hash()
    result = d.derive(red)
    recorded_version = result.metadata["derived"]["model_version"]
    assert recorded_version == "1.2.3"

    recomputed = _dhash(
        "clip_tags",
        f"{d.vision_model}+{d.text_model}",
        recorded_version,
        {"vocabulary": d.vocabulary, "top_k": d.top_k, "min_score": d.min_score},
    )
    assert recomputed == hash_, "derivation_hash() must exactly match what was recorded"


def test_model_version_falls_back_to_unknown_on_lookup_failure(monkeypatch):
    """A broken/absent metadata lookup must degrade, never crash."""
    import importlib.metadata

    def _raise(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _raise)
    assert ClipTagsDeriver._model_version() == "unknown"
