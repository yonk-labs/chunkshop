"""Tests for the `code_summary` extractor (SP-D).

The extractor stamps:
  - `metadata.summary`       per chunk (always, on non-empty input)
  - `metadata.file_summary`  on the first chunk of a file (heuristic), gated by
                              the config flag `file_summary=True`

Because chunkshop's `Extractor` protocol is `extract(text) -> ExtractResult`,
the runner only feeds text. To make file-level rollups possible without
touching `runner.py`, the extractor accepts an optional second kwarg
`chunk_metadata: dict | None = None`. Tests that exercise the file rollup
pass that kwarg explicitly.
"""
from __future__ import annotations

import warnings

import pytest

from chunkshop.config import CodeSummaryExtractor as Cfg, ExtractorConfig
from chunkshop.extractors import ExtractResult, load_extractor
from chunkshop.extractors.code_summary import CodeSummaryExtractor


# ---------------------------------------------------------------------------
# A tiny in-test summarizer used by the "callable" backend tests.
#
# The chunkshop summarizer contract is `summarize(text: str, **kwargs) -> str`.
# This module-level function satisfies that contract.
# ---------------------------------------------------------------------------
def _uppercase_prefix_summarize(text: str, **kwargs) -> str:
    """Returns the first 80 chars of `text`, upper-cased. Trivial test backend."""
    max_length = int(kwargs.get("max_length", 80))
    return text[:max_length].upper()


# ---------------------------------------------------------------------------
# Config discrimination / factory
# ---------------------------------------------------------------------------
def test_in_extractor_union():
    """pydantic accepts type=code_summary and validates fields."""
    cfg: ExtractorConfig = Cfg(type="code_summary")
    assert cfg.type == "code_summary"
    assert cfg.backend == "lede"  # default
    assert cfg.callable_path is None
    assert cfg.max_length == 300
    assert cfg.file_summary is True


def test_loads_via_factory():
    """load_extractor dispatches the code_summary discriminator."""
    extractor = load_extractor(Cfg(type="code_summary", backend="first_n_sentences"))
    assert isinstance(extractor, CodeSummaryExtractor)


# ---------------------------------------------------------------------------
# Backend: lede
# ---------------------------------------------------------------------------
def test_summary_lede_when_available():
    pytest.importorskip("lede")
    extractor = load_extractor(Cfg(type="code_summary", backend="lede", max_length=200))
    text = (
        "def add(a, b):\n"
        "    return a + b\n\n"
        "Adds two numbers and returns the sum. Used as a tiny demo helper "
        "across the test suite. Has no external dependencies."
    )
    result = extractor.extract(text)
    assert isinstance(result, ExtractResult)
    assert result.tags == []
    assert result.metadata["summary"]
    assert isinstance(result.metadata["summary"], str)


def test_summary_falls_back_to_first_n_when_lede_missing(monkeypatch):
    """If `lede` is requested but the import fails, fall through to first_n
    and emit one RuntimeWarning per process."""
    # Force the lede import inside the extractor to fail.
    import builtins

    real_import = builtins.__import__

    def _no_lede(name, *args, **kwargs):
        if name == "chunkshop.summarizers.lede" or name == "lede":
            raise ImportError("simulated missing lede")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_lede)

    # Reset the once-per-process flag so this test always sees the warning.
    CodeSummaryExtractor._lede_fallback_warned = False  # type: ignore[attr-defined]

    extractor = load_extractor(Cfg(type="code_summary", backend="lede"))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = extractor.extract("First. Second. Third. Fourth. Fifth.")

    assert result.metadata["summary"]  # non-empty
    # At least one RuntimeWarning about the fallback.
    assert any(issubclass(w.category, RuntimeWarning) for w in caught), (
        f"expected RuntimeWarning, got categories: {[w.category for w in caught]}"
    )


# ---------------------------------------------------------------------------
# Backend: first_n_sentences
# ---------------------------------------------------------------------------
def test_summary_first_n_sentences_baseline():
    """Stops when the joined sentence length would exceed `max_length`."""
    extractor = load_extractor(
        Cfg(type="code_summary", backend="first_n_sentences", max_length=10)
    )
    result = extractor.extract("A. B. C. D. E.")
    # Sentences are atomic — we take whole sentences until the budget is hit.
    # "A." (2) + " " + "B." (2) = 5 chars; adding " C." would put it at 8 — still
    # under 10. Adding " D." would put it at 11 — over. So we stop after "C.".
    # The exact boundary is allowed to land at either "A. B." or "A. B. C." as
    # long as it's <= 10 and a clean sentence boundary.
    summary = result.metadata["summary"]
    assert summary
    assert len(summary) <= 10
    assert summary.startswith("A.")


# ---------------------------------------------------------------------------
# Backend: callable
# ---------------------------------------------------------------------------
def test_summary_callable_backend():
    """callable_path resolves a module:function and is invoked lazily."""
    extractor = load_extractor(
        Cfg(
            type="code_summary",
            backend="callable",
            callable_path=(
                "tests.chunkshop.test_code_summary_extractor:_uppercase_prefix_summarize"
            ),
            max_length=80,
        )
    )
    result = extractor.extract("hello world, this is a small piece of code text")
    assert result.metadata["summary"] == "HELLO WORLD, THIS IS A SMALL PIECE OF CODE TEXT"


def test_summary_callable_bad_path_raises_clear_error():
    extractor = load_extractor(
        Cfg(type="code_summary", backend="callable", callable_path="no.such:thing")
    )
    with pytest.raises(ValueError) as exc:
        extractor.extract("some text")
    msg = str(exc.value).lower()
    # The error must point at the offending callable_path.
    assert "no.such" in msg or "callable_path" in msg


# ---------------------------------------------------------------------------
# file_summary rollup behavior (heuristic: first chunk per file via
# start_line == 1 OR symbol_type == "module")
# ---------------------------------------------------------------------------
def test_file_summary_stamped_on_first_chunk_per_file():
    extractor = load_extractor(
        Cfg(type="code_summary", backend="first_n_sentences", max_length=200)
    )
    text = "Computes the sum of two integers. Returns int. Used in tests."

    first_a = extractor.extract(text, chunk_metadata={"start_line": 1, "file_path": "a.py"})
    first_b = extractor.extract(
        text, chunk_metadata={"start_line": 1, "file_path": "b.py"}
    )
    mid = extractor.extract(
        text, chunk_metadata={"start_line": 42, "file_path": "a.py"}
    )

    assert first_a.metadata.get("file_summary")
    assert first_b.metadata.get("file_summary")
    # Mid-file chunk: per-chunk summary present, file_summary absent.
    assert mid.metadata.get("summary")
    assert "file_summary" not in mid.metadata or mid.metadata.get("file_summary") is None


def test_file_summary_disabled_skips_rollup():
    extractor = load_extractor(
        Cfg(
            type="code_summary",
            backend="first_n_sentences",
            file_summary=False,
            max_length=200,
        )
    )
    result = extractor.extract(
        "Hello world. Another sentence.", chunk_metadata={"start_line": 1}
    )
    assert "file_summary" not in result.metadata or result.metadata.get("file_summary") is None
    assert result.metadata["summary"]


def test_file_summary_fires_on_symbol_type_module():
    """symbol_type=='module' also flags 'first chunk of file' (codeparse hint)."""
    extractor = load_extractor(
        Cfg(type="code_summary", backend="first_n_sentences", max_length=200)
    )
    result = extractor.extract(
        "Module docstring. Second sentence here.",
        chunk_metadata={"symbol_type": "module"},
    )
    assert result.metadata.get("file_summary")


# ---------------------------------------------------------------------------
# Contract / shape
# ---------------------------------------------------------------------------
def test_extract_returns_empty_tags():
    extractor = load_extractor(Cfg(type="code_summary", backend="first_n_sentences"))
    result = extractor.extract("Hello. World.")
    assert isinstance(result, ExtractResult)
    assert result.tags == []
    assert "summary" in result.metadata


def test_max_length_respected():
    extractor = load_extractor(
        Cfg(type="code_summary", backend="first_n_sentences", max_length=20)
    )
    result = extractor.extract(
        "Alpha sentence. Beta sentence. Gamma sentence. Delta sentence."
    )
    # Whole-sentence boundaries — the summary should never exceed the budget by
    # more than a small slack (we accept the budget itself).
    assert len(result.metadata["summary"]) <= 20


def test_empty_text_returns_empty_summary():
    extractor = load_extractor(Cfg(type="code_summary", backend="first_n_sentences"))
    result = extractor.extract("")
    assert result.metadata["summary"] == ""


def test_extractor_idempotent():
    extractor = load_extractor(Cfg(type="code_summary", backend="first_n_sentences"))
    text = "First sentence. Second sentence. Third sentence."
    a = extractor.extract(text)
    b = extractor.extract(text)
    assert a.metadata["summary"] == b.metadata["summary"]


def test_dual_text_contract_preserved():
    """Extractor must not mutate input or carry state that leaks across calls
    beyond the documented `_lede_fallback_warned` flag."""
    extractor = load_extractor(Cfg(type="code_summary", backend="first_n_sentences"))
    text = "One. Two. Three."
    before = text
    extractor.extract(text)
    assert text == before
    result = extractor.extract(text)
    # Only metadata is contributed; no tags.
    assert result.tags == []
