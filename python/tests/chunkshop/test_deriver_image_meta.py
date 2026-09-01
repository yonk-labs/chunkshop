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


def test_separator_only_stem_no_path_segments(tmp_path):
    """Separator-only stem with path_segments=False triggers format fallback."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow comes with fastembed")
    p = tmp_path / "_.png"
    Image.new("RGB", (100, 100), (0, 0, 0)).save(p)
    text = ImageMetaDeriver(path_segments=False).derive(p).text
    assert text.strip(), "text must not be empty"
    assert "png image" in text.lower(), "format fallback should be used"


def test_separator_only_stem_with_limited_segments(tmp_path):
    """Separator-only stem but with path_segments uses directory names as fallback."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow comes with fastembed")
    subdir = tmp_path / "assets"
    subdir.mkdir()
    p = subdir / "_.jpg"
    Image.new("RGB", (100, 100), (0, 0, 0)).save(p)
    text = ImageMetaDeriver(path_segments=True, segment_depth=1).derive(p).text
    assert text.strip(), "text should use path segments when stem is empty"
    # Should contain 'assets' from the directory, not just the format token
    assert "assets" in text.lower()


def test_normal_case_fallback_never_fires(png):
    """Regression: normal filenames do not trigger the format fallback."""
    text = ImageMetaDeriver().derive(png).text
    assert "goblin" in text.lower()
    # Ensure format token fallback is NOT in the text (fallback did not fire)
    assert "png image" not in text.lower()
