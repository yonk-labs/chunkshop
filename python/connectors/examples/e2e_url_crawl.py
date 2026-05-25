#!/usr/bin/env python3
"""# Demo: URL ingest with depth crawl + ETag/Last-Modified cursor refresh

Hits a real public URL (default ``https://example.com``; small,
stable, no JS) and shows the depth-bounded HttpSource. Persists a
cursor at ``/tmp/chunkshop-demo-url-cursor.json``; the second run
sends ``If-None-Match`` and the server returns 304 → 0 fresh pages.

Run:
    python e2e_url_crawl.py
    python e2e_url_crawl.py --seed https://example.org --depth 1
    python e2e_url_crawl.py --reset

The demo handles network failure gracefully — it prints a clear
message instead of a traceback if the URL is unreachable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _bootstrap_repo_imports() -> None:
    """Self-bootstrap for raw `python e2e_*.py` runs in-repo."""
    here = Path(__file__).resolve()
    for d in (here.parents[1] / "src", here.parents[2] / "src"):
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))


_bootstrap_repo_imports()

import httpx

from chunkshop.chunkers import load_chunker
from chunkshop.config import HttpSource as Cfg
from chunkshop.config import SentenceAwareChunker as SentCfg
from chunkshop.sources.http import HttpSource
from chunkshop.testing import merge_cursor


CURSOR_PATH = Path("/tmp/chunkshop-demo-url-cursor.json")


def _print_banner() -> None:
    print("=" * 72)
    print("# Demo: URL crawl + depth + sentence_aware chunker + ETag cursor")
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
    parser.add_argument("--seed", default="https://example.com")
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv[1:])

    _print_banner()
    print(f"  seed:      {args.seed}")
    print(f"  depth:     {args.depth}")
    print(f"  max_pages: {args.max_pages}")

    if args.reset and CURSOR_PATH.exists():
        CURSOR_PATH.unlink()
        print("  cursor reset (deleted)")

    cursor = _load_cursor()
    print(f"  cursor:    {'EMPTY (first run)' if not cursor else f'{len(cursor)} URL(s) tracked'}")

    cfg = Cfg(
        type="http",
        urls=[args.seed],
        crawl_depth=args.depth,
        request_delay_seconds=0.5,
        respect_robots=True,
        max_pages=args.max_pages,
    )
    src = HttpSource(cfg)
    chunker = load_chunker(SentCfg(type="sentence_aware", min_chars=20, max_chars=400))

    try:
        docs = list(src.iter_changes_since(cursor))
    except httpx.ConnectError as exc:
        print(f"\nERROR: this demo needs network access. Could not connect:\n  {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPStatusError as exc:
        print(f"\nERROR: HTTP {exc.response.status_code} from {exc.request.url}", file=sys.stderr)
        return 3

    if not docs:
        print(f"\n  result: 0 fresh page(s) since last sync (cursor had {len(cursor)} URL(s))")
        _save_cursor(cursor)
        return 0

    print(f"\n  fetched {len(docs)} page(s):")
    total_chunks = 0
    total_chars = 0
    for doc in docs:
        chars = len(doc.content)
        total_chars += chars
        chunks = chunker.chunk(doc)
        total_chunks += len(chunks)
        etag = (doc.metadata or {}).get("etag")
        print(f"\n  - {doc.id}")
        print(f"      title: {doc.title!r}  chars: {chars}  chunks: {len(chunks)}  etag: {etag!r}")
        for i, c in enumerate(chunks[:2]):
            preview = c.embedded_content.replace("\n", " ").strip()[:60]
            print(f"      chunk[{i}]: {preview!r}")

    cursor = merge_cursor(src, cursor, docs)
    print(f"\n  total: {len(docs)} page(s), {total_chars:,} chars, {total_chunks} chunk(s)")
    _save_cursor(cursor)
    print("\n  done. Re-run to see the cursor short-circuit (server replies 304).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
