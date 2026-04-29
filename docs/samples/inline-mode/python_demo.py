#!/usr/bin/env python3
"""End-to-end demo of chunkshop's inline (library) mode for Python.

Run from the repo root:

    export VECTORS_DB_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
    cd python
    uv run python ../docs/samples/inline-mode/python_demo.py

What it does:
  1. Loads docs/samples/inline-mode/sample-inline.yaml.
  2. Builds a chunkshop.Pipeline (no source iteration; we drive it).
  3. Ingests three sales notes with metadata.
  4. Updates one of them with a longer body — verifies upsert + chunk replace.
  5. Updates one of them with a SHORTER body — verifies delete_orphans drops excess chunks.
  6. Deletes one of them — verifies delete_document removes its rows.
  7. Prints the final row count and a sample row.

Demonstrates: idempotent upsert, per-doc shrink cleanup (delete_orphans),
and explicit delete (delete_document) — the building blocks of an
in-process incremental pipeline driven by your application.
"""
from __future__ import annotations
import os
import pathlib
import sys

import psycopg

import chunkshop


YAML_PATH = pathlib.Path(__file__).parent / "sample-inline.yaml"


NOTE_001_V1 = """\
Customer: Acme Corp.

Met with Acme this morning. They're evaluating us against three competitors.
Main concerns: latency, support SLA, and onboarding time. We promised a
follow-up technical deep-dive next Tuesday.
"""

NOTE_001_V2_LONGER = """\
Customer: Acme Corp.

Met with Acme this morning. They're evaluating us against three competitors.
Main concerns: latency, support SLA, and onboarding time. We promised a
follow-up technical deep-dive next Tuesday.

UPDATE: Got a call back from their CTO. They want to see benchmark numbers
against their current vendor before we meet. Sending the bake-off doc and
the latency-percentile dashboard ahead of the meeting. Engineering on standby.
"""

NOTE_001_V3_SHORTER = "Acme deal closed. Moving to onboarding."

NOTE_002 = """\
Customer: Northwind Traders.

Northwind renewed for three years at the enterprise tier. Champion is
Sarah Chen on their data team. Risk: their procurement team flagged a
data-residency clause we'll need to negotiate at next renewal.
"""

NOTE_003 = """\
Customer: Globex Industries.

Discovery call. They have a 50-engineer team, currently on a competitor.
Pain point: prep latency before a query is too high. Demo scheduled for
Thursday.
"""


def show_count(label: str, dsn: str) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT doc_id, COUNT(*) AS chunks
            FROM rag.inline_chunks
            GROUP BY doc_id ORDER BY doc_id
        """)
        rows = cur.fetchall()
    print(f"  [{label}]")
    for doc_id, chunks in rows:
        print(f"    {doc_id}: {chunks} chunk(s)")
    if not rows:
        print("    (no rows)")


def main() -> int:
    dsn = os.environ.get("VECTORS_DB_DSN")
    if not dsn:
        print("VECTORS_DB_DSN not set. Example:")
        print("  export VECTORS_DB_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg")
        return 1
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        print(f"Could not reach Postgres at VECTORS_DB_DSN: {exc}")
        return 1

    print(f"Loading {YAML_PATH}")
    shop = chunkshop.Pipeline.from_yaml(YAML_PATH)

    print("\nStep 1: ingest 3 sales notes")
    n1 = shop.ingest_text(
        "note-001", NOTE_001_V1,
        metadata={"author": "rep_a", "account": "acme"},
    )
    n2 = shop.ingest_text(
        "note-002", NOTE_002,
        metadata={"author": "rep_b", "account": "northwind"},
    )
    n3 = shop.ingest_text(
        "note-003", NOTE_003,
        metadata={"author": "rep_a", "account": "globex"},
    )
    print(f"  wrote {n1} + {n2} + {n3} chunks")
    show_count("after initial ingest", dsn)

    print("\nStep 2: re-ingest note-001 with a LONGER body (upsert + new chunks)")
    n1b = shop.ingest_text(
        "note-001", NOTE_001_V2_LONGER,
        metadata={"author": "rep_a", "account": "acme", "revision": 2},
    )
    print(f"  wrote {n1b} chunks for note-001")
    show_count("after grow", dsn)

    print("\nStep 3: re-ingest note-001 with a SHORTER body (delete_orphans drops excess)")
    n1c = shop.ingest_text(
        "note-001", NOTE_001_V3_SHORTER,
        metadata={"author": "rep_a", "account": "acme", "revision": 3},
    )
    print(f"  wrote {n1c} chunks for note-001")
    show_count("after shrink", dsn)

    print("\nStep 4: delete note-002 explicitly")
    deleted = shop.delete_document("note-002")
    print(f"  deleted {deleted} row(s)")
    show_count("after delete", dsn)

    print("\nStep 5: inspect a sample row")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT doc_id, seq_num, source, account, '
            'left(original_content, 60) || \'...\' AS preview '
            "FROM rag.inline_chunks WHERE doc_id='note-001' ORDER BY seq_num LIMIT 1"
        )
        row = cur.fetchone()
    if row:
        print(f"  doc_id={row[0]} seq={row[1]} source={row[2]} account={row[3]}")
        print(f"  preview: {row[4]}")

    print("\nDone. Try `psql -d $VECTORS_DB_DSN -c 'SELECT doc_id, seq_num, account FROM rag.inline_chunks ORDER BY doc_id, seq_num'`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
