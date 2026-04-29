#!/usr/bin/env python3
"""run_incremental_watermark.py — Pattern B from docs/incremental.md.

Wraps `chunkshop ingest` with a durable cursor table so each run only sees
rows updated since the previous successful run. Templates the YAML's
`source.where` clause from the cursor + the source table's current
MAX(updated_at) before invoking chunkshop.

Usage:
    python3 scripts/run_incremental_watermark.py \\
        --source-tag sales_notes \\
        --source-dsn-env SALES_DB_DSN \\
        --target-dsn-env VECTORS_DB_DSN \\
        --config docs/samples/incremental-pg-table/sample.yaml \\
        --updated-column updated_at \\
        [--source-schema public] [--source-table sales_notes] \\
        [--cursor-schema chunkshop] [--cursor-table cursor]

The cursor table is created on first run:

    CREATE TABLE chunkshop.cursor (
        source_tag   text PRIMARY KEY,
        last_seen_at timestamptz NOT NULL,
        updated_at   timestamptz NOT NULL DEFAULT now()
    );

A Postgres-side advisory lock keyed on the source_tag prevents two
wrappers from racing on the same cursor row. If a previous run is in
flight, this script exits cleanly (rc=0) with a log line.

Why Python and not bash:
- pyyaml (already a chunkshop dep) round-trips YAML correctly, including
  multi-line block scalars and quoted strings the original sed/yq mix
  would mangle.
- The whole script is self-contained — psycopg, pyyaml, subprocess.
  Nothing in your shell PATH needs to change.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import psycopg
import yaml


IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-tag", required=True)
    p.add_argument("--source-dsn-env", required=True)
    p.add_argument("--target-dsn-env", required=True)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--updated-column", default="updated_at")
    p.add_argument("--source-schema", default="public")
    p.add_argument("--source-table", default=None,
                   help="defaults to --source-tag if omitted")
    p.add_argument("--cursor-schema", default="chunkshop")
    p.add_argument("--cursor-table", default="cursor")
    p.add_argument("--chunkshop-bin", default="chunkshop",
                   help="chunkshop binary or shell-tokenized prefix command. "
                        "Examples: 'chunkshop', '/path/to/.venv/bin/chunkshop', "
                        "'uv run --project python chunkshop'.")
    return p.parse_args()


def validate_ident(name: str, label: str) -> None:
    if not IDENT_RE.match(name):
        raise SystemExit(f"identifier {label}={name!r} must match {IDENT_RE.pattern}")


def advisory_lock_key(source_tag: str) -> int:
    """Stable 64-bit signed int for pg_try_advisory_lock — same algorithm
    chunkshop's sink uses for its schema lock so the two are consistent."""
    digest = hashlib.blake2b(source_tag.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


@contextmanager
def advisory_lock(dsn: str, key: int, source_tag: str):
    """Acquire pg_try_advisory_lock for the duration of the block.

    Returns False inside the block if the lock could not be taken (another
    wrapper is mid-run). The caller decides what to do (we exit 0 with a
    log line — see main).
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            ok = cur.fetchone()[0]
            conn.commit()
        try:
            yield ok
        finally:
            if ok:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
                    conn.commit()


def ensure_cursor_table(target_dsn: str, schema: str, table: str) -> None:
    fq = f'"{schema}"."{table}"'
    with psycopg.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {fq} (
                source_tag   text PRIMARY KEY,
                last_seen_at timestamptz NOT NULL,
                updated_at   timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.commit()


def read_last_seen(target_dsn: str, schema: str, table: str, source_tag: str):
    fq = f'"{schema}"."{table}"'
    with psycopg.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT last_seen_at FROM {fq} WHERE source_tag = %s",
            (source_tag,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def read_source_high_water(source_dsn: str, schema: str, table: str, col: str):
    fq = f'"{schema}"."{table}"'
    with psycopg.connect(source_dsn) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT MAX("{col}") FROM {fq}')
        return cur.fetchone()[0]


def write_cursor(target_dsn: str, schema: str, table: str, source_tag: str, new_high) -> None:
    fq = f'"{schema}"."{table}"'
    with psycopg.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {fq} (source_tag, last_seen_at)
            VALUES (%s, %s)
            ON CONFLICT (source_tag) DO UPDATE
            SET last_seen_at = EXCLUDED.last_seen_at,
                updated_at   = now()
            """,
            (source_tag, new_high),
        )
        conn.commit()


def render_yaml_with_where(config_path: Path, updated_col: str, last_seen, new_high) -> Path:
    """Load the YAML, set source.where to the windowed clause, write a temp
    file, return its path. The caller deletes it.

    Uses pyyaml — handles multi-line scalars, quoted strings, anchors. The
    original bash+sed approach corrupted any non-trivial source block.
    """
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    where_clause = (
        f'"{updated_col}" > \'{last_seen.isoformat()}\''
        f' AND "{updated_col}" <= \'{new_high.isoformat()}\''
    )
    cfg.setdefault("source", {})["where"] = where_clause
    fd, tmp_path = tempfile.mkstemp(suffix=".yaml", prefix="chunkshop-watermark-")
    os.close(fd)
    with open(tmp_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return Path(tmp_path)


def main() -> int:
    args = parse_args()
    source_table = args.source_table or args.source_tag
    for label, val in [
        ("source-tag", args.source_tag),
        ("updated-column", args.updated_column),
        ("source-schema", args.source_schema),
        ("source-table", source_table),
        ("cursor-schema", args.cursor_schema),
        ("cursor-table", args.cursor_table),
    ]:
        validate_ident(val, label)

    source_dsn = os.environ.get(args.source_dsn_env)
    target_dsn = os.environ.get(args.target_dsn_env)
    if not source_dsn:
        print(f"{args.source_dsn_env} not set", file=sys.stderr)
        return 2
    if not target_dsn:
        print(f"{args.target_dsn_env} not set", file=sys.stderr)
        return 2
    if not args.config.exists():
        print(f"config {args.config} not found", file=sys.stderr)
        return 2

    ensure_cursor_table(target_dsn, args.cursor_schema, args.cursor_table)

    lock_key = advisory_lock_key(args.source_tag)
    with advisory_lock(target_dsn, lock_key, args.source_tag) as got_lock:
        if not got_lock:
            print(
                f"another run for source_tag={args.source_tag} is in flight; exiting"
            )
            return 0

        last_seen = read_last_seen(
            target_dsn, args.cursor_schema, args.cursor_table, args.source_tag
        )
        new_high = read_source_high_water(
            source_dsn, args.source_schema, source_table, args.updated_column
        )
        if new_high is None:
            print(
                f"source_tag={args.source_tag} source table is empty; nothing to do"
            )
            return 0
        if last_seen is not None and new_high <= last_seen:
            print(
                f"source_tag={args.source_tag} no new rows since {last_seen.isoformat()}"
            )
            return 0

        # Use epoch as the lower bound on first run.
        from datetime import datetime, timezone
        if last_seen is None:
            last_seen = datetime(1970, 1, 1, tzinfo=timezone.utc)

        print(
            f"source_tag={args.source_tag} window: "
            f"({last_seen.isoformat()}, {new_high.isoformat()}]"
        )

        tmp_yaml = render_yaml_with_where(
            args.config, args.updated_column, last_seen, new_high
        )
        try:
            chunkshop_cmd = shlex.split(args.chunkshop_bin) + [
                "ingest", "--config", str(tmp_yaml),
            ]
            result = subprocess.run(chunkshop_cmd, check=False)
            if result.returncode != 0:
                print(
                    f"chunkshop ingest failed (rc={result.returncode}); "
                    "cursor not advanced",
                    file=sys.stderr,
                )
                return result.returncode
        finally:
            try:
                tmp_yaml.unlink()
            except FileNotFoundError:
                pass

        write_cursor(
            target_dsn, args.cursor_schema, args.cursor_table,
            args.source_tag, new_high,
        )
        print(
            f"source_tag={args.source_tag} advanced cursor to {new_high.isoformat()}"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
