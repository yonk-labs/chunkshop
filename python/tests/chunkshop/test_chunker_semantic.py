"""Correctness tests for SemanticChunker (SC-001, SC-004, SC-005)."""
import pytest

from chunkshop.chunkers.semantic import SemanticChunker
from chunkshop.config import SemanticChunker as Cfg
from chunkshop.sources.base import Document


# Fixture: three distinct topics with clear semantic shifts.
THREE_TOPIC_DOC = (
    "Neural networks are trained via backpropagation. "
    "Weights update based on loss gradients. "
    "Optimizers like Adam adapt learning rates per parameter. "
    "The golden retriever fetches the ball and wags its tail. "
    "Dogs are social animals that bond with humans. "
    "Border collies are particularly intelligent and trainable. "
    "Bread dough needs gluten development for structure. "
    "Knead the dough until it passes the windowpane test. "
    "Let it rise until doubled, then shape and proof before baking."
)


@pytest.mark.slow
def test_semantic_finds_three_topic_boundaries():
    chunker = SemanticChunker(Cfg(
        type="semantic",
        breakpoint_percentile=66,   # looser for this short test
        min_sentences_per_chunk=2,
    ))
    doc = Document(id="d1", content=THREE_TOPIC_DOC, title="mixed", metadata={})
    chunks = chunker.chunk(doc)
    # Should produce ~3 chunks (one per topic). Allow 2-4 given percentile sensitivity.
    assert 2 <= len(chunks) <= 4, f"got {len(chunks)} chunks"
    for c in chunks:
        assert c.metadata.get("strategy") == "semantic"
        assert len(c.original_content) <= 2000  # max_chunk_chars default


@pytest.mark.slow
def test_semantic_single_sentence_doc_returns_one_chunk():
    chunker = SemanticChunker(Cfg(type="semantic", min_sentences_per_chunk=1))
    doc = Document(id="d1", content="Just one sentence here.", title="t", metadata={})
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1
    assert chunks[0].metadata["strategy"] == "semantic"


@pytest.mark.slow
def test_semantic_max_chars_clipping():
    """Oversized semantic segment hard-splits on sentence boundary (SC-004)."""
    # ~7500 chars, one topic (near-identical sentences — no boundaries detected).
    long_text = " ".join(["The quick brown fox jumps over the lazy dog."] * 200)
    chunker = SemanticChunker(Cfg(
        type="semantic",
        max_chunk_chars=2000,
        min_sentences_per_chunk=1,
    ))
    doc = Document(id="d1", content=long_text, title="t", metadata={})
    chunks = chunker.chunk(doc)
    assert len(chunks) >= 2  # must have been split at least once
    for c in chunks:
        assert len(c.original_content) <= 2000


@pytest.mark.slow
def test_semantic_empty_content_returns_no_chunks():
    chunker = SemanticChunker(Cfg(type="semantic"))
    doc = Document(id="d1", content="", title="t", metadata={})
    assert chunker.chunk(doc) == []
    doc2 = Document(id="d2", content="   \n  ", title="t", metadata={})
    assert chunker.chunk(doc2) == []


@pytest.mark.slow
def test_semantic_min_sentences_merges_small_spans():
    """Chunks below min_sentences_per_chunk merge into neighbors (SC-005)."""
    # Three-topic doc but with min_sentences_per_chunk=10 — no single topic has 10
    # sentences, so the result should be collapsed into one chunk (everything merges
    # forward, and the final one merges backward).
    chunker = SemanticChunker(Cfg(
        type="semantic",
        breakpoint_percentile=50,
        min_sentences_per_chunk=10,
    ))
    doc = Document(id="d1", content=THREE_TOPIC_DOC, title="t", metadata={})
    chunks = chunker.chunk(doc)
    # With min_sentences=10 and only 9 sentences, we should get exactly 1 chunk.
    assert len(chunks) == 1


@pytest.mark.slow
def test_semantic_sequential_seq_nums():
    chunker = SemanticChunker(Cfg(
        type="semantic",
        breakpoint_percentile=66,
        min_sentences_per_chunk=1,
    ))
    doc = Document(id="d1", content=THREE_TOPIC_DOC, title="t", metadata={})
    chunks = chunker.chunk(doc)
    for i, c in enumerate(chunks):
        assert c.seq_num == i
        assert c.doc_id == "d1"


@pytest.mark.slow
def test_semantic_embedded_content_equals_original():
    """SemanticChunker does not rewrite embedded_content (unlike hierarchy)."""
    chunker = SemanticChunker(Cfg(type="semantic", min_sentences_per_chunk=1))
    doc = Document(id="d1", content="One. Two. Three.", title="t", metadata={})
    chunks = chunker.chunk(doc)
    for c in chunks:
        assert c.embedded_content == c.original_content


@pytest.mark.slow
def test_semantic_boundary_model_same_reuses_main_embedder():
    """SC-002: boundary_model='same' must not double the model footprint.

    We verify by passing a main_embedder_model_name and checking the chunker's
    resolved model name matches. The shared-instance check (one TextEmbedding)
    is exercised via load_chunker wiring in a separate test.
    """
    chunker = SemanticChunker(
        Cfg(type="semantic", boundary_model="same"),
        main_embedder_model_name="Xenova/bge-base-en-v1.5-int8",
    )
    assert chunker._model_name == "Xenova/bge-base-en-v1.5-int8"


def test_semantic_boundary_model_same_without_main_raises():
    with pytest.raises(ValueError, match="requires main_embedder_model_name"):
        SemanticChunker(Cfg(type="semantic", boundary_model="same"))


def test_semantic_shared_model_reuses_instance():
    """SC-002: passing shared_model must reuse it without loading a second one."""

    class StubModel:
        def __init__(self):
            self.embed_calls = 0

        def embed(self, sentences):
            import numpy as np
            self.embed_calls += 1
            # Return deterministic 4-dim embeddings so the chunker sees a real
            # cosine signal; identical vectors = no boundaries.
            return iter([np.ones(4, dtype=np.float32) for _ in sentences])

    stub = StubModel()
    chunker = SemanticChunker(
        Cfg(type="semantic", boundary_model="same", min_sentences_per_chunk=1),
        main_embedder_model_name="Xenova/bge-base-en-v1.5-int8",
        shared_model=stub,
    )
    assert chunker._model is stub  # reuses; no fastembed load
    doc = Document(id="d1", content="One. Two. Three. Four.", title="t", metadata={})
    chunks = chunker.chunk(doc)
    assert stub.embed_calls == 1  # exactly one embed call across chunking
    assert chunker._model is stub  # still the shared instance


def test_load_chunker_passes_shared_boundary_model_for_same():
    """SC-002 end-to-end: load_chunker propagates shared_boundary_model to the
    semantic chunker when boundary_model='same'."""
    from chunkshop.chunkers import load_chunker
    from chunkshop.config import FastembedEmbedder

    class Sentinel:
        def embed(self, texts):
            raise AssertionError("should not be called")

    sentinel = Sentinel()
    main_embed_cfg = FastembedEmbedder(
        type="fastembed", model_name="Xenova/bge-base-en-v1.5-int8", dim=768,
    )
    chunker = load_chunker(
        Cfg(type="semantic", boundary_model="same"),
        main_embedder=main_embed_cfg,
        shared_boundary_model=sentinel,
    )
    assert chunker._model is sentinel
    assert chunker._model_name == "Xenova/bge-base-en-v1.5-int8"


def test_load_chunker_does_not_share_when_dedicated_boundary_model():
    """Dedicated boundary model path must NOT attach the shared_boundary_model,
    otherwise we'd embed sentences with the wrong model."""
    from chunkshop.chunkers import load_chunker

    class Sentinel:
        pass

    sentinel = Sentinel()
    chunker = load_chunker(
        Cfg(type="semantic"),  # default boundary_model = MiniLM int8
        shared_boundary_model=sentinel,
    )
    assert chunker._model is None  # lazy-load on first chunk()
    assert chunker._model_name == "sentence-transformers/all-MiniLM-L6-v2-int8"
