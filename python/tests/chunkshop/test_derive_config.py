import pytest

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.derivers import load_deriver
from chunkshop.sources.derivers.image_meta import ImageMetaDeriver


def test_image_meta_rung_loads():
    d = load_deriver({"rung": "image_meta"})
    assert isinstance(d, ImageMetaDeriver)


def test_clip_tags_rung_loads_with_vocabulary():
    from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

    d = load_deriver({"rung": "clip_tags", "vocabulary": ["a cat", "a dog"]})
    assert isinstance(d, ClipTagsDeriver)
    assert d.vocabulary == ["a cat", "a dog"]


def test_unknown_rung_is_rejected():
    with pytest.raises(ValueError, match="unknown rung"):
        load_deriver({"rung": "telepathy"})


def test_derive_config_accepts_pipe_separated_extensions():
    cfg = Cfg(
        type="files",
        glob="/tmp/**/*",
        derive={"png|jpg|webp": {"rung": "image_meta"}},
    )
    assert "png|jpg|webp" in cfg.derive


def test_typo_in_derive_block_is_rejected():
    """extra='forbid' must catch config typos, not ignore them."""
    with pytest.raises(Exception):
        Cfg(
            type="files",
            glob="/tmp/**/*",
            derive={"png": {"rung": "clip_tags", "vocabluary": ["x"]}},
        )
