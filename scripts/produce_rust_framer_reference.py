"""Produce Python reference output for the Rust framer parity tests.

Run from the chunkshop repo root:
    /home/yonk/yonk-tools/chunkshop/python/.venv/bin/python \
        scripts/produce_rust_framer_reference.py

Writes JSON references for heading_boundary + jsonpath framers, each holding:
    { "raw_id": ..., "raw_title": ..., "config": {...},
      "frames": [ { "id", "content", "title", "metadata" }, ... ] }
"""
from __future__ import annotations

import json
from pathlib import Path

from chunkshop.framers.heading_boundary import HeadingBoundaryFramer
from chunkshop.framers.jsonpath import JSONPathFramer
from chunkshop.config import (
    HeadingBoundaryFramerConfig,
    JSONPathFramerConfig,
)
from chunkshop.sources.base import Document


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"


def doc_to_dict(d: Document) -> dict:
    return {
        "id": d.id,
        "content": d.content,
        "title": d.title,
        "metadata": d.metadata,
    }


def produce_heading_boundary() -> None:
    text = (FIXTURE_DIR / "framer_heading_corpus.md").read_text(encoding="utf-8")
    cfg = HeadingBoundaryFramerConfig(
        type="heading_boundary",
        pattern=r"^##\s",
        title_from_heading=True,
    )
    f = HeadingBoundaryFramer(cfg)
    raw = Document(id="fixture", content=text, title=None, metadata={})
    frames = f.frame(raw)
    payload = {
        "raw_id": raw.id,
        "raw_title": raw.title,
        "config": {
            "pattern": cfg.pattern,
            "title_from_heading": cfg.title_from_heading,
        },
        "frames": [doc_to_dict(d) for d in frames],
    }
    out = FIXTURE_DIR / "framer_heading_reference.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(frames)} frames)")


def produce_jsonpath() -> None:
    text = (FIXTURE_DIR / "framer_jsonpath_corpus.json").read_text(encoding="utf-8")
    cfg = JSONPathFramerConfig(
        type="jsonpath",
        row_path="items.*",
        title_path="title",
        body_path="body",
    )
    f = JSONPathFramer(cfg)
    raw = Document(id="fixture", content=text, title=None, metadata={})
    frames = f.frame(raw)
    payload = {
        "raw_id": raw.id,
        "raw_title": raw.title,
        "config": {
            "row_path": cfg.row_path,
            "title_path": cfg.title_path,
            "body_path": cfg.body_path,
        },
        "frames": [doc_to_dict(d) for d in frames],
    }
    out = FIXTURE_DIR / "framer_jsonpath_reference.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(frames)} frames)")


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    produce_heading_boundary()
    produce_jsonpath()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
