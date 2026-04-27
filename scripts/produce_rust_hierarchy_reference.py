"""Produce reference chunks for the Rust hierarchy chunker parity test.

Run from the chunkshop repo root:
    uv run --project python python scripts/produce_rust_hierarchy_reference.py
"""
from __future__ import annotations

import json
from pathlib import Path

from chunkshop.chunkers.hierarchy import HierarchyChunker
from chunkshop.config import HierarchyChunker as Cfg
from chunkshop.sources.base import Document


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"


def main() -> int:
    corpus_path = FIXTURE_DIR / "hierarchy_corpus.txt"
    out_path = FIXTURE_DIR / "hierarchy_reference.json"

    text = corpus_path.read_text(encoding="utf-8")
    cfg = Cfg(type="hierarchy", prefix_heading=True, min_section_chars=100, max_chars=400)
    chunker = HierarchyChunker(cfg)
    doc = Document(id="fixture", content=text, title=None, metadata={})
    chunks = chunker.chunk(doc)

    payload = {
        "doc_id": doc.id,
        "doc_title": doc.title,
        "config": {
            "prefix_heading": cfg.prefix_heading,
            "min_section_chars": cfg.min_section_chars,
            "max_chars": cfg.max_chars,
        },
        "chunks": [
            {
                "seq_num": c.seq_num,
                "original_content": c.original_content,
                "embedded_content": c.embedded_content,
                "heading": c.metadata.get("heading", ""),
                "section_part": c.metadata.get("section_part", 0),
            }
            for c in chunks
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out_path} ({len(chunks)} chunks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
