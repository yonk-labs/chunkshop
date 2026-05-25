#!/usr/bin/env python3
"""# Demo: S3 bucket ingest + sentence_aware chunker + ETag cursor refresh

Uses the same ``_FakeS3`` pattern the core test suite uses in
``tests/chunkshop/test_s3_incremental.py``. Installs a fake ``boto3``
module that returns an in-memory paginator, so ``chunkshop.sources.s3``
runs end-to-end without any AWS calls.

Run:
    python e2e_s3_mocked.py
    python e2e_s3_mocked.py --mutate   # change one ETag; second run re-emits only it

Cursor at ``/tmp/chunkshop-demo-s3-cursor.json``.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path
from typing import Any

from chunkshop.chunkers import load_chunker
from chunkshop.config import SentenceAwareChunker as SentCfg


CURSOR_PATH = Path("/tmp/chunkshop-demo-s3-cursor.json")


class _FakeS3:
    """Minimal boto3-shaped fake. Returns Contents + bodies from a list."""

    def __init__(self, objs):
        # objs: list of (key, etag, body_bytes)
        self.objs = objs

    def get_paginator(self, _):
        objs = self.objs

        class _P:
            def paginate(self, **kw):
                yield {
                    "Contents": [
                        {"Key": k, "ETag": e, "Size": len(b)} for k, e, b in objs
                    ]
                }

        return _P()

    def get_object(self, Bucket, Key):
        for k, e, b in self.objs:
            if k == Key:
                return {"Body": types.SimpleNamespace(read=lambda b=b: b), "ETag": e}
        raise KeyError(Key)


def _install_fake_boto3(objs):
    fake = types.ModuleType("boto3")
    fake.client = lambda *a, **k: _FakeS3(objs)
    sys.modules["boto3"] = fake


def _baseline_objects():
    return [
        ("docs/intro.md", '"etag-intro-1"', b"# Intro\n\nWelcome to the bucket. This is the intro document. It is short."),
        ("docs/guide.md", '"etag-guide-1"', b"# Guide\n\nThis is the guide. It has multiple sentences. Each sentence should chunk into a piece."),
        ("docs/notes.txt", '"etag-notes-1"', b"Notes go here. They are unstructured. We still chunk them with sentence_aware."),
        ("logs/2026-01.log", '"etag-log-1"', b"INFO: startup ok\nWARN: cache cold\nINFO: serving requests"),
        ("data/changelog.md", '"etag-changelog-1"', b"# Changelog\n\n- v1: initial release\n- v2: bug fixes"),
    ]


def _mutated_objects():
    objs = _baseline_objects()
    # Bump guide.md's ETag + body — only this object should re-emit.
    objs[1] = ("docs/guide.md", '"etag-guide-2"', b"# Guide v2\n\nUpdated guide. Significantly expanded with three new sentences here.")
    return objs


def _print_banner() -> None:
    print("=" * 72)
    print("# Demo: S3 bucket ingest (mocked boto3) + sentence_aware + ETag cursor")
    print("=" * 72)


def _load_cursor() -> dict[str, Any]:
    if CURSOR_PATH.exists():
        try:
            return json.loads(CURSOR_PATH.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {}


def _save_cursor(cursor: dict[str, Any]) -> None:
    CURSOR_PATH.write_text(json.dumps(cursor, indent=2))
    print(f"  cursor persisted -> {CURSOR_PATH}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete the persisted cursor before running")
    parser.add_argument("--mutate", action="store_true", help="mutate one ETag so only it re-emits")
    args = parser.parse_args(argv[1:])

    _print_banner()
    if args.reset and CURSOR_PATH.exists():
        CURSOR_PATH.unlink()
        print("  cursor reset (deleted)")

    objs = _mutated_objects() if args.mutate else _baseline_objects()
    _install_fake_boto3(objs)

    from chunkshop.config import S3Source as Cfg
    from chunkshop.sources.s3 import S3Source
    from chunkshop.testing import merge_cursor

    src = S3Source(Cfg(type="s3", bucket="acme-bucket"))
    cursor = _load_cursor()
    print(f"  cursor: {'EMPTY (first run)' if not cursor else f'{len(cursor)} known key(s)'}")
    print(f"  objects in bucket: {len(objs)}")
    if args.mutate:
        print("  (--mutate active: docs/guide.md ETag changed to etag-guide-2)")

    chunker = load_chunker(SentCfg(type="sentence_aware", min_chars=20, max_chars=400))

    docs = list(src.iter_changes_since(cursor))
    if not docs:
        print("\n  result: 0 changes since last sync (all ETags matched cursor)")
        _save_cursor(cursor)
        return 0

    print(f"\n  ingested {len(docs)} object(s):")
    total_chunks = 0
    for doc in docs:
        chunks = chunker.chunk(doc)
        total_chunks += len(chunks)
        print(f"\n  - {doc.id}  ({len(chunks)} chunk(s))  etag={doc.metadata.get('etag')}")
        for i, c in enumerate(chunks[:3]):
            preview = c.embedded_content.replace("\n", " ").strip()[:60]
            print(f"      chunk[{i}]: {preview!r}")
        if len(chunks) > 3:
            print(f"      ... ({len(chunks) - 3} more)")
    cursor = merge_cursor(src, cursor, docs)
    print(f"\n  total: {len(docs)} object(s), {total_chunks} chunk(s)")
    _save_cursor(cursor)
    print("\n  done. Re-run without flags to see 0 changes; with --mutate to see incremental.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
