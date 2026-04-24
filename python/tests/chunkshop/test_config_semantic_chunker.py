"""Config tests for SemanticChunker (SC-001)."""
import pytest
from pydantic import ValidationError

from chunkshop.config import SemanticChunker


def test_semantic_chunker_defaults():
    c = SemanticChunker(type="semantic")
    assert c.boundary_model == "sentence-transformers/all-MiniLM-L6-v2-int8"
    assert c.breakpoint_percentile == 95
    assert c.min_sentences_per_chunk == 3
    # Per 2026-04-21 plan annotation: default aligns with chunker-max-chars hotfix.
    assert c.max_chunk_chars == 2000
    assert c.sentence_splitter == "naive"


def test_semantic_chunker_same_boundary_model():
    c = SemanticChunker(type="semantic", boundary_model="same")
    assert c.boundary_model == "same"


def test_semantic_chunker_rejects_bad_percentile():
    with pytest.raises(ValidationError):
        SemanticChunker(type="semantic", breakpoint_percentile=150)
    with pytest.raises(ValidationError):
        SemanticChunker(type="semantic", breakpoint_percentile=0)


def test_semantic_chunker_rejects_bad_min_sentences():
    with pytest.raises(ValidationError):
        SemanticChunker(type="semantic", min_sentences_per_chunk=0)


def test_semantic_chunker_rejects_tiny_max_chars():
    with pytest.raises(ValidationError):
        SemanticChunker(type="semantic", max_chunk_chars=50)


def test_semantic_chunker_rejects_bad_sentence_splitter():
    with pytest.raises(ValidationError):
        SemanticChunker(type="semantic", sentence_splitter="spacy")


def test_semantic_chunker_rejects_extra_fields():
    with pytest.raises(ValidationError):
        SemanticChunker(type="semantic", unknown_field=True)
