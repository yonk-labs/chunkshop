#!/usr/bin/env python3
"""# Demo: GitHub repo ingest + code_aware chunker + refresh-only-changes incremental

Hits a real public GitHub repo (default: ``octocat/Hello-World``), walks
files, chunks each through ``code_aware`` (functions/classes for .py;
sentence_aware fallback for prose/markdown), and persists a cursor JSON
so the second run shows zero new changes.

Run:
    python e2e_github_with_code_chunker.py
    python e2e_github_with_code_chunker.py --owner psf --repo cpython --branch main --limit 5
    GITHUB_TOKEN=ghp_xxx python e2e_github_with_code_chunker.py   # higher rate limit

The cursor lives at ``/tmp/chunkshop-demo-github-cursor.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from chunkshop.chunkers import load_chunker
from chunkshop.config import (
    CodeAwareChunker as CodeAwareCfg,
)


CURSOR_PATH = Path("/tmp/chunkshop-demo-github-cursor.json")


def _print_banner() -> None:
    print("=" * 72)
    print("# Demo: GitHub repo ingest + code_aware chunker + incremental refresh")
    print("=" * 72)


def _load_cursor() -> dict[str, Any]:
    if CURSOR_PATH.exists():
        try:
            return json.loads(CURSOR_PATH.read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"  (warn) could not parse cursor at {CURSOR_PATH}: {exc}; treating as empty")
    return {}


def _save_cursor(cursor: dict[str, Any]) -> None:
    CURSOR_PATH.write_text(json.dumps(cursor, indent=2))
    print(f"  cursor persisted -> {CURSOR_PATH}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", default="octocat")
    parser.add_argument("--repo", default="Hello-World")
    parser.add_argument("--branch", default="master")  # Hello-World uses master
    parser.add_argument("--limit", type=int, default=20, help="max files to chunk in one run (default 20)")
    parser.add_argument("--reset", action="store_true", help="delete the persisted cursor before running")
    args = parser.parse_args(argv[1:])

    _print_banner()
    print(f"  target: github.com/{args.owner}/{args.repo}@{args.branch} (limit={args.limit})")
    token = os.environ.get("GITHUB_TOKEN")
    print(f"  token: {'set (Authorization will be sent)' if token else 'unset (anonymous; ~60 req/hr)'}")

    if args.reset and CURSOR_PATH.exists():
        CURSOR_PATH.unlink()
        print("  cursor reset (deleted)")

    cursor = _load_cursor()
    is_first_run = not cursor
    print(f"  cursor: {'EMPTY (first run)' if is_first_run else cursor}")

    from chunkshop_connectors.github import factory

    cfg: dict[str, Any] = {
        "owner": args.owner,
        "repo": args.repo,
        "branch": args.branch,
    }
    if token:
        cfg["token"] = token

    try:
        src = factory(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: could not instantiate the github connector: {exc}", file=sys.stderr)
        return 1

    chunker = load_chunker(CodeAwareCfg(type="code_aware", min_chars=50, max_chars=4000))

    try:
        emitted = 0
        total_chunks = 0
        latest_cursor: dict[str, Any] = dict(cursor)
        for doc in src.iter_changes_since(cursor):
            if emitted >= args.limit:
                print(f"  (limit {args.limit} hit; stopping iteration early)")
                break
            chunks = chunker.chunk(doc)
            total_chunks += len(chunks)
            emitted += 1
            print(f"\n  [{emitted:3d}] {doc.id}  ({len(chunks)} chunk(s))")
            for i, chunk in enumerate(chunks[:4]):
                kind = chunk.metadata.get("node_type") or chunk.metadata.get("strategy", "?")
                name = chunk.metadata.get("node_name") or ""
                preview = chunk.embedded_content.replace("\n", " ").strip()[:60]
                tag = f"{kind}:{name}" if name else kind
                print(f"        - chunk[{i}] {tag:<24} | {preview!r}")
            if len(chunks) > 4:
                print(f"        ... ({len(chunks) - 4} more chunk(s) omitted)")
            # Carry cursor forward; cursor_from is monotonic so the last value wins.
            latest_cursor.update(src.cursor_from(doc))
    except httpx.ConnectError as exc:
        print(f"\nERROR: this demo needs network access to {cfg.get('base_url', 'api.github.com')}", file=sys.stderr)
        print(f"  underlying error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPStatusError as exc:
        print(f"\nERROR: GitHub returned {exc.response.status_code}: {exc.response.text[:200]}", file=sys.stderr)
        if exc.response.status_code == 403:
            print("  (likely rate-limited — set GITHUB_TOKEN for 5000 req/hr)", file=sys.stderr)
        return 3

    print()
    if emitted == 0:
        print(f"  result: 0 changes since last sync (cursor was {cursor})")
    else:
        print(f"  result: ingested {emitted} document(s), {total_chunks} chunk(s) total")
    _save_cursor(latest_cursor)
    print("\n  done. Re-run this script to verify the cursor short-circuits to 0 changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
