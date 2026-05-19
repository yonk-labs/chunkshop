"""Unit tests for ConsolidationChunker (episode + fact emission, resilience)."""
from chunkshop.config import (ConsolidationChunker as Cfg, SentenceAwareChunker,
                              CallableConsolidator, PassthroughConsolidator)
from chunkshop.chunkers import load_chunker
from chunkshop.sources.base import Document
import sys, types


def _episode_doc():
    return Document(id="s1", content="[user] a b c d e. f g h i j.",
                    title=None,
                    metadata={"session_id": "s1", "frame_seq": 0,
                              "_episode_events": [{"role": "user",
                                  "content": "a b c d e. f g h i j.", "ts": 1.0}]})


def _cfg(consolidator):
    return Cfg(type="consolidation",
               base=SentenceAwareChunker(type="sentence_aware", doc_type="prose"),
               consolidator=consolidator)


def test_emits_episode_and_fact_chunks():
    mod = types.ModuleType("fk")
    mod.consolidate = lambda text, **kw: {"summary": "SUM",
        "facts": [{"subject": "x", "predicate": "p", "object": "y",
                   "support_span": "x p y", "confidence": 0.5}]}
    sys.modules["fk"] = mod
    ch = load_chunker(_cfg(CallableConsolidator(mode="callable", module="fk")))
    chunks = ch.chunk(_episode_doc())
    kinds = [c.metadata.get("kind") for c in chunks]
    assert "episode" in kinds and "fact" in kinds
    ep = next(c for c in chunks if c.metadata["kind"] == "episode")
    assert ep.embedded_content == "SUM"
    assert ep.original_content
    fa = next(c for c in chunks if c.metadata["kind"] == "fact")
    assert fa.embedded_content == "x p y"
    assert fa.metadata["subject"] == "x" and fa.metadata["predicate"] == "p"
    assert fa.metadata["source_chunk_seq"] == ep.seq_num
    assert "_episode_events" not in ep.metadata


def test_callable_failure_degrades_to_passthrough():
    mod = types.ModuleType("boom")
    def _raise(text, **kw):
        raise RuntimeError("llm down")
    mod.consolidate = _raise
    sys.modules["boom"] = mod
    ch = load_chunker(_cfg(CallableConsolidator(mode="callable", module="boom")))
    chunks = ch.chunk(_episode_doc())
    assert [c.metadata["kind"] for c in chunks] == ["episode"]
    ep = chunks[0]
    assert ep.metadata.get("consolidation_error")
    assert ep.embedded_content == ep.original_content


def test_fact_support_span_length_capped():
    big = "w " * 5000
    mod = types.ModuleType("big")
    mod.consolidate = lambda text, **kw: {"summary": "s",
        "facts": [{"subject": None, "predicate": None, "object": None,
                   "support_span": big, "confidence": None}]}
    sys.modules["big"] = mod
    cfg = _cfg(CallableConsolidator(mode="callable", module="big"))
    cfg = cfg.model_copy(update={"fact_max_chars": 50})
    ch = load_chunker(cfg)
    fa = next(c for c in ch.chunk(_episode_doc()) if c.metadata["kind"] == "fact")
    assert len(fa.embedded_content) <= 50 and fa.metadata["truncated"] is True
