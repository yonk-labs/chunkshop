"""Produce Python reference chunks for the Rust fixed_overlap + neighbor_expand
parity tests. Run from the chunkshop repo root:

    uv run --project python python scripts/produce_rust_chunker_batch2_reference.py
"""
from __future__ import annotations

import json
from pathlib import Path

from chunkshop.chunkers.fixed_overlap import FixedOverlapChunker
from chunkshop.chunkers.hierarchy import HierarchyChunker
from chunkshop.chunkers.neighbor_expand import NeighborExpandChunker
from chunkshop.config import (
    FixedOverlapChunker as FixedOverlapCfg,
    HierarchyChunker as HierarchyCfg,
    NeighborExpandChunker as NeighborExpandCfg,
)
from chunkshop.sources.base import Document


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"


def chunk_to_dict(c) -> dict:
    return {
        "seq_num": c.seq_num,
        "original_content": c.original_content,
        "embedded_content": c.embedded_content,
        "metadata": c.metadata,
    }


def produce_fixed_overlap() -> None:
    text = (FIXTURE_DIR / "fixed_overlap_corpus.txt").read_text(encoding="utf-8")
    cfg = FixedOverlapCfg(type="fixed_overlap", window_words=100, step_words=50)
    chunker = FixedOverlapChunker(cfg)
    doc = Document(id="fixture", content=text, title=None, metadata={})
    chunks = chunker.chunk(doc)
    payload = {
        "doc_id": doc.id,
        "doc_title": doc.title,
        "config": {"window_words": cfg.window_words, "step_words": cfg.step_words},
        "chunks": [chunk_to_dict(c) for c in chunks],
    }
    out = FIXTURE_DIR / "fixed_overlap_reference.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(chunks)} chunks)")


def produce_neighbor_expand() -> None:
    text = (FIXTURE_DIR / "neighbor_expand_corpus.txt").read_text(encoding="utf-8")
    base_cfg = HierarchyCfg(
        type="hierarchy", prefix_heading=True, min_section_chars=100, max_chars=400
    )
    base = HierarchyChunker(base_cfg)
    cfg = NeighborExpandCfg(type="neighbor_expand", base=base_cfg, window=1)
    chunker = NeighborExpandChunker(cfg, base)
    doc = Document(id="fixture", content=text, title=None, metadata={})
    chunks = chunker.chunk(doc)
    payload = {
        "doc_id": doc.id,
        "doc_title": doc.title,
        "config": {
            "window": cfg.window,
            "base": {
                "type": "hierarchy",
                "prefix_heading": base_cfg.prefix_heading,
                "min_section_chars": base_cfg.min_section_chars,
                "max_chars": base_cfg.max_chars,
            },
        },
        "chunks": [chunk_to_dict(c) for c in chunks],
    }
    out = FIXTURE_DIR / "neighbor_expand_reference.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(chunks)} chunks)")


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    produce_fixed_overlap()
    produce_neighbor_expand()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
