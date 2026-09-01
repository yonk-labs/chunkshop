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
