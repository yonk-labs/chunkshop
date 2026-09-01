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


@pytest.mark.parametrize(
    "path_segments,segment_depth",
    [(True, 3), (False, 2), (True, 1)],
)
def test_derivation_hash_matches_what_stamp_provenance_records(
    png, path_segments, segment_depth
):
    """Key/record agreement — the params-drift tripwire.

    ``ImageMetaDeriver`` states its params TWICE: once in
    ``derivation_hash()`` (which keys the cache) and once in the
    ``stamp_provenance(...)`` call inside ``derive()`` (which records what
    produced the output). They agree today. If someone added a param to one
    and not the other, the cache key would stop tracking a parameter that
    changes the output, and a config change would silently be served stale
    entries. ``clip_tags`` has had an agreement test since it shipped;
    rung 0 had none.

    Every value here is read back out of the RECORD — including
    ``params_hash``, which is the only component the two literals can
    disagree about. Restating the params as a third literal in the test
    would defeat the whole point: it would agree with whichever side
    happened to match it and stay green. (A previous version of the
    ``clip_tags`` test hardcoded ``"python"`` for ``engine`` and would have
    missed exactly that class of drift.)

    Parametrized over non-default configs too: a params literal that
    hardcoded a default instead of reading ``self`` would pass at
    ``(True, 3)`` and fail here.
    """
    from chunkshop.sources.derivers.provenance import _hash_from_parts

    d = ImageMetaDeriver(path_segments=path_segments, segment_depth=segment_depth)
    rec = d.derive(png).metadata["derived"]

    recomputed = _hash_from_parts(
        rec["rung"],
        rec["model"],
        rec["model_version"],
        rec["engine"],
        rec["params_hash"],
    )
    assert recomputed == d.derivation_hash(), (
        "derivation_hash() and the provenance stamped by derive() disagree — "
        "the two params literals in image_meta.py have drifted, so the cache "
        "key no longer tracks everything that changes the output"
    )
    # The stamp must describe THIS deriver, not a default-constructed one;
    # without this the assertion above would still pass if both sides ignored
    # `self` in the same way.
    assert rec["engine"] == "python"
    assert rec["rung"] == "image_meta"


def test_the_tripwire_actually_fires_when_the_two_literals_drift(png, monkeypatch):
    """Proof that the test above is not vacuous.

    Simulates the exact defect: `derive()` stamps params that
    `derivation_hash()` does not know about. If the assertion in the previous
    test could not see that, it would be decoration.
    """
    from chunkshop.sources.derivers import image_meta as im
    from chunkshop.sources.derivers.provenance import _hash_from_parts, stamp_provenance

    def drifted(result, **kwargs):
        kwargs["params"] = {**kwargs["params"], "sneaky_new_param": True}
        return stamp_provenance(result, **kwargs)

    monkeypatch.setattr(im, "stamp_provenance", drifted)
    d = ImageMetaDeriver()
    rec = d.derive(png).metadata["derived"]
    recomputed = _hash_from_parts(
        rec["rung"], rec["model"], rec["model_version"], rec["engine"], rec["params_hash"]
    )
    assert recomputed != d.derivation_hash()
