"""Tests for the sentence splitting helpers used by SemanticChunker."""
import pytest

from chunkshop.chunkers._sentence_split import (
    load_sentence_splitter,
    naive_sentences,
)


def test_naive_splits_on_terminators():
    text = "First sentence. Second sentence! Third? Fourth sentence."
    sents = naive_sentences(text)
    assert len(sents) == 4
    assert sents[0].startswith("First")
    assert sents[1].startswith("Second")
    assert sents[2].startswith("Third")
    assert sents[3].startswith("Fourth")


def test_naive_handles_no_terminators():
    sents = naive_sentences("just some words with no terminator")
    assert sents == ["just some words with no terminator"]


def test_naive_strips_empty():
    sents = naive_sentences("One. . Two.")
    # The lone "." between two sentences should not create an empty item.
    # We require: at least two non-empty items survive.
    assert all(s.strip() for s in sents)
    assert len(sents) >= 2


def test_naive_handles_empty_string():
    assert naive_sentences("") == []
    assert naive_sentences("   ") == []


def test_load_sentence_splitter_naive():
    fn = load_sentence_splitter("naive")
    assert callable(fn)
    result = fn("A. B. C.")
    assert isinstance(result, list)
    assert len(result) == 3


def test_load_sentence_splitter_unknown_raises():
    with pytest.raises(ValueError, match="unknown sentence_splitter"):
        load_sentence_splitter("spacy")
