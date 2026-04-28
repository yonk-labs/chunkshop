"""Tests for chunkshop.summarizers shim adapters (SC-005b)."""
import importlib.util

import pytest


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


SAMPLE = (
    "The Roman Empire spanned three continents at its height. It collapsed in the west in 476 CE. "
    "Eastern half survived as the Byzantine Empire for another thousand years. Its capital "
    "Constantinople was a center of learning and trade. The fall of Constantinople in 1453 "
    "marked the end of the medieval era."
)


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_lede_shim_returns_string():
    from chunkshop.summarizers.lede import summarize
    s = summarize(SAMPLE, max_length=200)
    assert isinstance(s, str), f"expected str, got {type(s).__name__}"
    assert s  # non-empty
    # Sanity: extractive summary should be no longer than input.
    assert len(s) <= len(SAMPLE)


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_lede_shim_empty_input_returns_empty():
    from chunkshop.summarizers.lede import summarize
    assert summarize("") == ""
    assert summarize("   \n  ") == ""


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_lex_rank():
    from chunkshop.summarizers.sumy import summarize
    s = summarize(SAMPLE, algorithm="lex_rank", sentences_count=2)
    assert isinstance(s, str)
    assert s
    assert len(s) < len(SAMPLE)  # 2 sentences out of 5 should be shorter


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_text_rank():
    from chunkshop.summarizers.sumy import summarize
    s = summarize(SAMPLE, algorithm="text_rank", sentences_count=2)
    assert isinstance(s, str)
    assert s


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_lsa():
    from chunkshop.summarizers.sumy import summarize
    s = summarize(SAMPLE, algorithm="lsa", sentences_count=2)
    assert s


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_unknown_algorithm_raises():
    from chunkshop.summarizers.sumy import summarize
    with pytest.raises(ValueError, match="unknown sumy algorithm"):
        summarize(SAMPLE, algorithm="nope")


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_empty_input_returns_empty():
    from chunkshop.summarizers.sumy import summarize
    assert summarize("") == ""
    assert summarize("   \n  ") == ""
