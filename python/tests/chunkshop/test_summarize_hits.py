"""Tests for search_common.summarize_hits (I-15).

A fake summarize_fn keeps these unit tests lede-free; one test exercises the
real lede summarizer (skipped if lede is not installed) to prove a caption
survives via heading-prepend that extractive compression would otherwise drop.
"""
from __future__ import annotations

import pytest

from chunkshop.search_common import Hit, summarize_hits


def _hit(doc_id, seq, text, heading=None, embedded=None):
    meta = {}
    if heading is not None:
        meta["heading"] = heading
    return Hit(
        doc_id=doc_id,
        seq_num=seq,
        text=text,
        score=1.0,
        metadata=meta,
        legs=("semantic",),
        embedded_text=embedded if embedded is not None else "",
    )


# A fake summarizer: echoes the (truncated) body and records the kwargs it saw.
class _FakeSummarizer:
    def __init__(self):
        self.last_kwargs = None
        self.last_text = None

    def __call__(self, text, **kwargs):
        self.last_kwargs = dict(kwargs)
        self.last_text = text
        return text[: kwargs.get("max_length", 100)]


def test_empty_hits_returns_empty():
    fake = _FakeSummarizer()
    assert summarize_hits([], fake) == ""
    assert fake.last_text is None  # summarize_fn never called


def test_headings_prepended_deduped_and_ordered():
    hits = [
        _hit("d1", 0, "body one", heading="Apple v. Pepper"),
        _hit("d2", 0, "body two", heading="apple v. pepper"),  # case-dupe
        _hit("d3", 0, "body three", heading="Roe v. Wade"),
    ]
    fake = _FakeSummarizer()
    out = summarize_hits(hits, fake, max_length=10_000, use_embedded=False)
    lines = out.splitlines()
    # First two lines are the deduped headings in hit order, then a blank line.
    assert lines[0] == "Apple v. Pepper"
    assert lines[1] == "Roe v. Wade"
    assert lines[2] == ""
    # The case-variant dupe was dropped.
    assert out.lower().count("apple v. pepper") == 1


def test_headings_capped_at_five():
    hits = [_hit(f"d{i}", 0, f"body {i}", heading=f"Case {i}") for i in range(8)]
    fake = _FakeSummarizer()
    out = summarize_hits(hits, fake, max_length=10_000, use_embedded=False)
    heading_lines = out.split("\n\n")[0].splitlines()
    assert heading_lines == [f"Case {i}" for i in range(5)]
    assert "Case 5" not in out.split("\n\n")[0]


def test_prepend_headings_false_omits_headings():
    hits = [_hit("d1", 0, "body one", heading="Apple v. Pepper")]
    fake = _FakeSummarizer()
    out = summarize_hits(
        hits, fake, max_length=10_000, prepend_headings=False, use_embedded=False
    )
    assert "Apple v. Pepper" not in out
    assert out == "body one"


def test_hints_gated_off_when_none():
    hits = [_hit("d1", 0, "body one")]
    fake = _FakeSummarizer()
    summarize_hits(hits, fake, use_embedded=False)
    assert "hints" not in fake.last_kwargs
    assert "hint_focus" not in fake.last_kwargs
    assert "hint_mode" not in fake.last_kwargs


def test_hints_forwarded_when_provided():
    hits = [_hit("d1", 0, "body one")]
    fake = _FakeSummarizer()
    summarize_hits(
        hits, fake, hints=["foo", "bar"], hint_focus=0.9, hint_mode="hard",
        use_embedded=False,
    )
    assert fake.last_kwargs["hints"] == ["foo", "bar"]
    assert fake.last_kwargs["hint_focus"] == 0.9
    assert fake.last_kwargs["hint_mode"] == "hard"


def test_uses_embedded_text_when_use_embedded_true():
    hits = [_hit("d1", 0, "raw body", heading="H", embedded="HEADING\n\nraw body")]
    fake = _FakeSummarizer()
    summarize_hits(hits, fake, max_length=10_000, prepend_headings=False)
    assert fake.last_text == "HEADING\n\nraw body"


def test_use_embedded_falls_back_to_text_when_embedded_empty():
    hits = [_hit("d1", 0, "raw body", embedded="")]
    fake = _FakeSummarizer()
    summarize_hits(hits, fake, max_length=10_000, prepend_headings=False)
    assert fake.last_text == "raw body"


def test_use_embedded_false_uses_text():
    hits = [_hit("d1", 0, "raw body", embedded="EMBEDDED")]
    fake = _FakeSummarizer()
    summarize_hits(
        hits, fake, max_length=10_000, prepend_headings=False, use_embedded=False
    )
    assert fake.last_text == "raw body"


def test_real_lede_caption_survives_via_prepend():
    """With the REAL extractive summarizer, the case caption is compressed out
    of the body but re-attached by prepend_headings — so it survives."""
    lede = pytest.importorskip("lede")  # noqa: F841
    from chunkshop.summarizers import lede as lede_shim

    caption = "Apple Inc. v. Pepper"
    # A body whose salient sentences don't mention the caption, so extractive
    # compression to a short summary drops it.
    body = (
        "The plaintiffs alleged monopolization of the app distribution market. "
        "The court analyzed standing under the antitrust laws at length. "
        "Direct purchasers may sue for the overcharge they pay. "
        "The remedy turns on who transacts directly with the alleged monopolist. "
        "The dissent disagreed about the scope of the doctrine."
    )
    hits = [
        Hit(
            doc_id="d1",
            seq_num=0,
            text=body,
            score=1.0,
            metadata={"heading": caption},
            legs=("semantic",),
            embedded_text=f"{caption}\n\n{body}",
        )
    ]
    # Without prepend, the short summary should not surface the caption.
    no_prepend = summarize_hits(
        hits, lede_shim.summarize, max_length=120, prepend_headings=False
    )
    # With prepend (default), the caption is re-attached.
    with_prepend = summarize_hits(hits, lede_shim.summarize, max_length=120)

    assert caption not in no_prepend, (
        f"expected caption compressed out, got: {no_prepend!r}"
    )
    assert with_prepend.startswith(caption)
    assert caption in with_prepend
