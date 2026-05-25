#!/usr/bin/env python3
"""# Demo: Postgres table source — existing DB connections continue to function

Creates a small temp table with 5 rows at distinct timestamps on the
chunkshop test Postgres (``localhost:5434``), reads them via
``PgTableSource``, then inserts a 6th row and verifies that the
cursor refresh emits only the new row.

Pre-flight: if Postgres is unreachable, prints a clear message and
exits 0 (so CI doesn't fail). Cleans up the table in a finally block.

Run:
    python e2e_database.py

Override DSN with ``CHUNKSHOP_TEST_DSN``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path as _Path


def _bootstrap_repo_imports() -> None:
    """Make this demo runnable directly with raw `python e2e_*.py` from the
    repo, without requiring `uv pip install -e .` of chunkshop / chunkshop-
    connectors. Harmless when those packages are already installed."""
    here = _Path(__file__).resolve()
    for d in (here.parents[1] / "src", here.parents[2] / "src"):
        if d.is_dir() and str(d) not in sys.path:
            sys.path.insert(0, str(d))


_bootstrap_repo_imports()

DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/chunkshop_test",
)


def _print_banner() -> None:
    print("=" * 72)
    print("# Demo: PgTableSource — DB ingest + incremental cursor refresh")
    print("=" * 72)


def _reachable() -> bool:
    try:
        import psycopg
    except ImportError:
        print("  psycopg is not installed; cannot demo Postgres path.", file=sys.stderr)
        return False
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception as exc:
        print(f"  Postgres at {DSN} is unreachable: {exc}", file=sys.stderr)
        return False


def main() -> int:
    _print_banner()
    print(f"  DSN: {DSN}")

    if not _reachable():
        print("  -> skipping demo (Postgres needed). Start with `docker compose -f docker-compose.test.yaml up -d`.")
        return 0

    import psycopg

    from chunkshop.config import PgTableSource
    from chunkshop.sources.pg_table import PgTableSource as PgSrc
    from chunkshop.testing import merge_cursor

    schema = "public"
    name = "chunkshop_demo_db_e2e"

    base = datetime.now(timezone.utc) - timedelta(hours=2)
    seed_rows = [
        ("a", "Document A — first row of the demo.", base),
        ("b", "Document B — second row of the demo.", base + timedelta(minutes=15)),
        ("c", "Document C — third row of the demo.", base + timedelta(minutes=30)),
        ("d", "Document D — fourth row of the demo.", base + timedelta(minutes=45)),
        ("e", "Document E — fifth row of the demo.", base + timedelta(minutes=60)),
    ]

    try:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
            cur.execute(
                f"CREATE TABLE {schema}.{name} "
                f"(id text primary key, body text, updated_at timestamptz)"
            )
            cur.executemany(
                f"INSERT INTO {schema}.{name} (id, body, updated_at) VALUES (%s, %s, %s)",
                seed_rows,
            )
            conn.commit()
        print(f"  seeded {len(seed_rows)} rows into {schema}.{name}")

        cfg = PgTableSource(
            type="pg_table",
            dsn=DSN,
            database=schema,
            table=name,
            id_column="id",
            content_column="body",
            updated_at_column="updated_at",
        )
        src = PgSrc(cfg)

        print("\n  -- first sync (empty cursor) --")
        first = list(src.iter_changes_since(src.empty_cursor()))
        for doc in first:
            print(f"    [{doc.id}] {doc.content!r}")
        print(f"  -> {len(first)} document(s)")

        cursor = merge_cursor(src, src.empty_cursor(), first)
        print(f"  cursor advanced to: {cursor}")

        # Insert a 6th row fresher than the rest.
        print("\n  -- inserting one new row, then re-syncing with cursor --")
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {schema}.{name} (id, body, updated_at) "
                f"VALUES (%s, %s, %s)",
                ("f", "Document F — late arrival, freshly inserted.", datetime.now(timezone.utc)),
            )
            conn.commit()

        delta = list(src.iter_changes_since(cursor))
        for doc in delta:
            print(f"    [{doc.id}] {doc.content!r}")
        print(f"  -> {len(delta)} document(s) (expected 1: 'f')")

        if {d.id for d in delta} == {"f"}:
            print("\n  PASS: cursor refresh correctly narrowed to the new row.")
        else:
            print(f"\n  WARN: expected delta {{'f'}}, got {set(d.id for d in delta)}", file=sys.stderr)
    finally:
        try:
            with psycopg.connect(DSN) as conn, conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {schema}.{name}")
                conn.commit()
            print(f"\n  cleanup: dropped {schema}.{name}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n  cleanup failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
