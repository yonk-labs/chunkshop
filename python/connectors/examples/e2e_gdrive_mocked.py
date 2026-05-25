#!/usr/bin/env python3
"""# Demo: Google Drive folder ingest + sentence_aware chunker + cursor refresh

Uses the hermetic Drive v3 mock from
``chunkshop_connectors.testing.mocks.gdrive.make_gdrive_mock`` so it
runs offline. Simulates a Drive folder with several docs and walks the
full pipeline: list → fetch → chunk → emit. Persists a cursor at
``/tmp/chunkshop-demo-gdrive-cursor.json``; the second run finds no
new changes; queueing a synthetic "new file" change shows incremental
delivery.

Run:
    python e2e_gdrive_mocked.py
    python e2e_gdrive_mocked.py --add-change   # simulate one new file arriving

No network. No OAuth tokens needed.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

from chunkshop.chunkers import load_chunker
from chunkshop.config import SentenceAwareChunker as SentCfg


CURSOR_PATH = Path("/tmp/chunkshop-demo-gdrive-cursor.json")


def _print_banner() -> None:
    print("=" * 72)
    print("# Demo: Google Drive ingest (mocked) + sentence_aware chunker + cursor")
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
    parser.add_argument(
        "--add-change",
        action="store_true",
        help="queue a synthetic new-file change in the mock so the second sync sees one delta",
    )
    args = parser.parse_args(argv[1:])

    _print_banner()
    if args.reset and CURSOR_PATH.exists():
        CURSOR_PATH.unlink()
        print("  cursor reset (deleted)")

    from chunkshop_connectors.gdrive import factory
    from chunkshop_connectors.testing.mocks.gdrive import make_gdrive_mock

    mock = make_gdrive_mock()
    # Beef up the seed set with a third text doc so chunks > 1.
    mock.add_file(
        file_id="file-doc-2",
        name="Onboarding Plan",
        mime_type="application/vnd.google-apps.document",
        content=(
            "Welcome to the team. This is the onboarding plan.\n"
            "We will start with the basics. Then move to the deep dives.\n"
            "Bring questions to office hours."
        ),
    )
    if args.add_change:
        mock.add_file(
            file_id="file-delta-1",
            name="late.txt",
            mime_type="text/plain",
            content=b"Just-arrived file body. Will only appear on incremental.",
        )
        mock.add_change("file-delta-1", new_start_page_token="TOKEN_AFTER_ADD")

    src = factory(mock.valid_config)
    src._transport = mock.transport
    src._reset_client()

    cursor = _load_cursor()
    print(f"  cursor: {'EMPTY (first run)' if not cursor else cursor}")

    chunker = load_chunker(SentCfg(type="sentence_aware", min_chars=20, max_chars=400))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # mock skips an image; suppress noise
        docs = list(src.iter_changes_since(cursor))

    if not docs:
        print("\n  result: 0 changes since last sync (cursor short-circuited)")
        _save_cursor(cursor)
        return 0

    print(f"\n  ingested {len(docs)} document(s):")
    total_chunks = 0
    latest = dict(cursor)
    for doc in docs:
        chunks = chunker.chunk(doc)
        total_chunks += len(chunks)
        print(f"\n  - {doc.id!r}  title={doc.title!r}  ({len(chunks)} chunk(s))")
        for i, c in enumerate(chunks[:3]):
            preview = c.embedded_content.replace("\n", " ").strip()[:60]
            print(f"      chunk[{i}]: {preview!r}")
        if len(chunks) > 3:
            print(f"      ... ({len(chunks) - 3} more chunk(s) omitted)")
        latest.update(src.cursor_from(doc))

    print(f"\n  total: {len(docs)} doc(s), {total_chunks} chunk(s)")
    _save_cursor(latest)
    print("\n  done. Re-run without --add-change to see the 0-changes path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
