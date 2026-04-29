"""Produce Python reference chunks for the Rust summary-chunker parity tests.

Run from the chunkshop repo root:
    /home/yonk/yonk-tools/chunkshop/python/.venv/bin/python \
        scripts/produce_rust_summary_reference.py

Writes JSON references for two scenarios per chunker:
  - summary_embed with passthrough summarizer + hierarchy base
  - summary_embed with external summarizer (pulls from doc.metadata.summary)
  - hierarchical_summary with passthrough + fixed_n grouping + hierarchy base
  - hierarchical_summary with passthrough + section_aware grouping + hierarchy base
"""
from __future__ import annotations

import json
from pathlib import Path

from chunkshop.chunkers.hierarchical_summary import HierarchicalSummaryChunker
from chunkshop.chunkers.hierarchy import HierarchyChunker
from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.config import (
    ExternalSummarizer as ExternalSummarizerCfg,
    FixedNGrouping as FixedNGroupingCfg,
    HierarchicalSummaryChunker as HierarchicalSummaryCfg,
    HierarchyChunker as HierarchyCfg,
    PassthroughSummarizer as PassthroughSummarizerCfg,
    SectionAwareGrouping as SectionAwareGroupingCfg,
    SummaryEmbedChunker as SummaryEmbedCfg,
)
from chunkshop.sources.base import Document


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"


def chunk_to_dict(c) -> dict:
    return {
        "doc_id": c.doc_id,
        "seq_num": c.seq_num,
        "original_content": c.original_content,
        "embedded_content": c.embedded_content,
        "metadata": c.metadata,
    }


def produce_summary_embed_passthrough() -> None:
    text = (FIXTURE_DIR / "hierarchy_corpus.txt").read_text(encoding="utf-8")
    base_cfg = HierarchyCfg(
        type="hierarchy", prefix_heading=True, min_section_chars=100, max_chars=400
    )
    base = HierarchyChunker(base_cfg)
    cfg = SummaryEmbedCfg(
        type="summary_embed",
        base=base_cfg,
        summarizer=PassthroughSummarizerCfg(mode="passthrough"),
    )
    chunker = SummaryEmbedChunker(cfg, base)
    doc = Document(id="fixture", content=text, title=None, metadata={})
    chunks = chunker.chunk(doc)
    payload = {
        "doc_id": doc.id,
        "config": {
            "summarizer_mode": "passthrough",
            "base": {
                "type": "hierarchy",
                "prefix_heading": True,
                "min_section_chars": 100,
                "max_chars": 400,
            },
        },
        "chunks": [chunk_to_dict(c) for c in chunks],
    }
    out = FIXTURE_DIR / "summary_embed_passthrough_reference.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(chunks)} chunks)")


def produce_summary_embed_external() -> None:
    text = (FIXTURE_DIR / "hierarchy_corpus.txt").read_text(encoding="utf-8")
    base_cfg = HierarchyCfg(
        type="hierarchy", prefix_heading=True, min_section_chars=100, max_chars=400
    )
    base = HierarchyChunker(base_cfg)
    cfg = SummaryEmbedCfg(
        type="summary_embed",
        base=base_cfg,
        summarizer=ExternalSummarizerCfg(mode="external", field="summary"),
    )
    chunker = SummaryEmbedChunker(cfg, base)
    pre_summary = "PRE_COMPUTED_SUMMARY_FOR_DOC"
    doc = Document(id="fixture", content=text, title=None, metadata={"summary": pre_summary})
    chunks = chunker.chunk(doc)
    payload = {
        "doc_id": doc.id,
        "config": {
            "summarizer_mode": "external",
            "field": "summary",
            "base": {
                "type": "hierarchy",
                "prefix_heading": True,
                "min_section_chars": 100,
                "max_chars": 400,
            },
        },
        "doc_metadata": {"summary": pre_summary},
        "chunks": [chunk_to_dict(c) for c in chunks],
    }
    out = FIXTURE_DIR / "summary_embed_external_reference.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(chunks)} chunks)")


def produce_hierarchical_summary_fixed_n() -> None:
    text = (FIXTURE_DIR / "hierarchy_corpus.txt").read_text(encoding="utf-8")
    base_cfg = HierarchyCfg(
        type="hierarchy", prefix_heading=True, min_section_chars=100, max_chars=400
    )
    base = HierarchyChunker(base_cfg)
    cfg = HierarchicalSummaryCfg(
        type="hierarchical_summary",
        base=base_cfg,
        summarizer=PassthroughSummarizerCfg(mode="passthrough"),
        grouping=FixedNGroupingCfg(strategy="fixed_n", n=3),
    )
    chunker = HierarchicalSummaryChunker(cfg, base)
    doc = Document(id="fixture", content=text, title=None, metadata={})
    chunks = chunker.chunk(doc)
    payload = {
        "doc_id": doc.id,
        "config": {
            "summarizer_mode": "passthrough",
            "grouping": {"strategy": "fixed_n", "n": 3},
            "base": {
                "type": "hierarchy",
                "prefix_heading": True,
                "min_section_chars": 100,
                "max_chars": 400,
            },
        },
        "chunks": [chunk_to_dict(c) for c in chunks],
    }
    out = FIXTURE_DIR / "hierarchical_summary_fixed_n_reference.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(chunks)} chunks)")


def produce_hierarchical_summary_section_aware() -> None:
    text = (FIXTURE_DIR / "hierarchy_corpus.txt").read_text(encoding="utf-8")
    base_cfg = HierarchyCfg(
        type="hierarchy", prefix_heading=True, min_section_chars=100, max_chars=400
    )
    base = HierarchyChunker(base_cfg)
    cfg = HierarchicalSummaryCfg(
        type="hierarchical_summary",
        base=base_cfg,
        summarizer=PassthroughSummarizerCfg(mode="passthrough"),
        grouping=SectionAwareGroupingCfg(strategy="section_aware"),
    )
    chunker = HierarchicalSummaryChunker(cfg, base)
    doc = Document(id="fixture", content=text, title=None, metadata={})
    chunks = chunker.chunk(doc)
    payload = {
        "doc_id": doc.id,
        "config": {
            "summarizer_mode": "passthrough",
            "grouping": {"strategy": "section_aware"},
            "base": {
                "type": "hierarchy",
                "prefix_heading": True,
                "min_section_chars": 100,
                "max_chars": 400,
            },
        },
        "chunks": [chunk_to_dict(c) for c in chunks],
    }
    out = FIXTURE_DIR / "hierarchical_summary_section_aware_reference.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(chunks)} chunks)")


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    produce_summary_embed_passthrough()
    produce_summary_embed_external()
    produce_hierarchical_summary_fixed_n()
    produce_hierarchical_summary_section_aware()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
