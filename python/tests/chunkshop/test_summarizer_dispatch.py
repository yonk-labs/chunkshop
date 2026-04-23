"""Unit tests for chunkshop.chunkers._summarizer.build_summarizer."""
import pytest

from chunkshop.config import (
    ExternalSummarizer,
    CallableSummarizer,
    PassthroughSummarizer,
)
from chunkshop.chunkers._summarizer import build_summarizer


def test_passthrough_returns_text_unchanged():
    fn = build_summarizer(PassthroughSummarizer(mode="passthrough"))
    assert fn("hello world", {}) == "hello world"
    assert fn("multiline\ntext", {"k": "v"}) == "multiline\ntext"


def test_external_pulls_from_metadata():
    fn = build_summarizer(ExternalSummarizer(mode="external", field="abstract"))
    assert fn("raw body", {"abstract": "a short summary"}) == "a short summary"


def test_external_raises_on_missing_field():
    fn = build_summarizer(ExternalSummarizer(mode="external", field="summary"))
    with pytest.raises(RuntimeError, match="no field 'summary'"):
        fn("x", {"other": "value"})


def test_external_raises_on_non_string_value():
    fn = build_summarizer(ExternalSummarizer(mode="external", field="summary"))
    with pytest.raises(RuntimeError, match="must be a string"):
        fn("x", {"summary": 123})


def test_callable_imports_and_invokes():
    # json.dumps fits the contract: (obj, **kwargs) -> str.
    fn = build_summarizer(CallableSummarizer(
        mode="callable", module="json", function="dumps",
        kwargs={"indent": 2},
    ))
    result = fn("hello", {})
    assert result == '"hello"'


def test_callable_import_failure_raises_clearly():
    cfg = CallableSummarizer(
        mode="callable", module="nonexistent_pkg_xyz_summer", function="summarize"
    )
    with pytest.raises(RuntimeError, match="could not import"):
        build_summarizer(cfg)


def test_callable_missing_attribute_raises():
    cfg = CallableSummarizer(
        mode="callable", module="json", function="this_function_does_not_exist",
    )
    with pytest.raises(RuntimeError, match="has no attribute"):
        build_summarizer(cfg)
