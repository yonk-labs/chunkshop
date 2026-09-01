import pytest

from chunkshop.sources.derivers.image_meta import ImageMetaDeriver


@pytest.fixture
def png(tmp_path):
    Image = pytest.importorskip("PIL.Image", reason="Pillow comes with fastembed")
    p = tmp_path / "sprites" / "goblin_warrior.png"
    p.parent.mkdir(parents=True)
    Image.new("RGBA", (64, 32), (255, 0, 0, 255)).save(p)
    return p


def test_emits_dimensions_and_format(png):
    result = ImageMetaDeriver().derive(png)
    assert result.metadata["width"] == 64
    assert result.metadata["height"] == 32
    assert result.metadata["image_format"] == "PNG"
    assert result.metadata["has_alpha"] is True
    assert result.metadata["aspect_ratio"] == pytest.approx(2.0)


def test_text_includes_humanised_filename(png):
    text = ImageMetaDeriver().derive(png).text
    assert "goblin" in text.lower()
    assert "warrior" in text.lower()


def test_text_includes_path_segments(png):
    text = ImageMetaDeriver().derive(png).text
    assert "sprites" in text.lower()


def test_provenance_is_stamped(png):
    md = ImageMetaDeriver().derive(png).metadata
    assert md["content_origin"] == "derived"
    assert md["derived_rung"] == "image_meta"
    assert md["derived"]["model"] == "deterministic"


def test_never_returns_empty_text(png):
    """Rung 0 is hallucination-proof AND always produces something."""
    assert ImageMetaDeriver().derive(png).text.strip()
