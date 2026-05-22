"""L2 (SC-009): per-doc hints override static; absent falls back; wrong type raises."""
import sys
import types
import pytest

from chunkshop.chunkers._summarizer import build_summarizer
from chunkshop.config import CallableSummarizer

# ---------------------------------------------------------------------------
# Spy infrastructure: register a fake module in sys.modules so that
# import_module("_test_spy_summarizer") resolves to a known object.
# ---------------------------------------------------------------------------
_SEEN: dict = {}

_spy_mod = types.ModuleType("_test_spy_summarizer")


def _spy(text, **kwargs):
    _SEEN.clear()
    _SEEN.update(kwargs)
    return text


_spy_mod._spy = _spy
sys.modules["_test_spy_summarizer"] = _spy_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**extra):
    return CallableSummarizer(
        mode="callable",
        module="_test_spy_summarizer",
        function="_spy",
        kwargs={"hints": ["static"], "hint_focus": 0.5},
        **extra,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_perdoc_hints_override_static():
    fn = build_summarizer(_cfg(hints_from_meta="lede_hints"))
    fn("body", {"lede_hints": ["perdoc"]})
    assert _SEEN["hints"] == ["perdoc"]


def test_absent_meta_falls_back_to_static():
    fn = build_summarizer(_cfg(hints_from_meta="lede_hints"))
    fn("body", {})
    assert _SEEN["hints"] == ["static"]


def test_wrong_type_raises_with_field_name():
    fn = build_summarizer(_cfg(hint_focus_from_meta="focus"))
    with pytest.raises(RuntimeError) as exc:
        fn("body", {"focus": "not-a-float"})
    assert "focus" in str(exc.value)


def test_bool_hint_focus_rejected():
    fn = build_summarizer(_cfg(hint_focus_from_meta="focus"))
    with pytest.raises(RuntimeError, match="bool"):
        fn("body", {"focus": True})
