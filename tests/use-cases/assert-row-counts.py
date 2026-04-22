#!/usr/bin/env python3
"""Post-run row-count assertions for tests/use-cases/ scenarios.

Mirror of tests/sub/assert-row-counts.py — same schema expectations, same
skip-on-missing-DSN semantics, different target schema.

Reads each scenario's expected.json and verifies the resulting table has at least
`min_rows` total rows, at least `min_docs` distinct doc_ids, and (if declared)
every key in `metadata_keys_required` appears in at least one row's metadata.

expected.json shape::

    {
      "table": "support_helpdesk",
      "min_rows": 5,
      "min_docs": 4,
      "metadata_keys_required": ["strategy", "heading"]
    }

Skips cleanly (exit 0) if CHUNKSHOP_TEST_DSN is unset — safe for CI matrices
without Postgres. Non-zero exit on any failure; prints the expected-vs-actual
delta for each failing scenario.

Invoke from repo root::

    python tests/use-cases/assert-row-counts.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCHEMA = "chunkshop_use_cases"
SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def _iter_expectations():
    for expected_path in sorted(SCENARIOS_DIR.glob("*/expected.json")):
        scenario = expected_path.parent.name
        data = json.loads(expected_path.read_text())
        yield scenario, data


def _check_table(cur, scenario: str, spec: dict) -> list[str]:
    table = spec["table"]
    min_rows = int(spec.get("min_rows", 1))
    min_docs = int(spec.get("min_docs", 1))
    required_keys = list(spec.get("metadata_keys_required", []))

    failures: list[str] = []

    # Row count
    cur.execute(f'SELECT COUNT(*) FROM "{SCHEMA}"."{table}"')
    rows = cur.fetchone()[0]
    if rows < min_rows:
        failures.append(f"{scenario}: rows={rows} < min_rows={min_rows}")

    # Distinct doc count
    cur.execute(f'SELECT COUNT(DISTINCT doc_id) FROM "{SCHEMA}"."{table}"')
    docs = cur.fetchone()[0]
    if docs < min_docs:
        failures.append(f"{scenario}: distinct doc_ids={docs} < min_docs={min_docs}")

    # Metadata keys — spot-check that at least one row mentions each required key
    for key in required_keys:
        cur.execute(
            f'SELECT COUNT(*) FROM "{SCHEMA}"."{table}" '
            f"WHERE metadata ? %s",
            (key,),
        )
        hits = cur.fetchone()[0]
        if hits == 0:
            failures.append(f"{scenario}: metadata key {key!r} never appears")

    if not failures:
        print(f"[use-cases] OK  {scenario}: rows={rows} docs={docs}")
    return failures


def main() -> int:
    dsn = os.environ.get("CHUNKSHOP_TEST_DSN")
    if not dsn:
        print("SKIP: CHUNKSHOP_TEST_DSN not set — skipping row-count assertions")
        return 0

    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        print("SKIP: psycopg not installed — skipping row-count assertions")
        return 0

    all_failures: list[str] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for scenario, spec in _iter_expectations():
                try:
                    all_failures.extend(_check_table(cur, scenario, spec))
                except psycopg.Error as exc:
                    all_failures.append(f"{scenario}: query error ({exc.__class__.__name__}): {exc}")
                    conn.rollback()

    if all_failures:
        print("\n[use-cases] FAILURES:")
        for f in all_failures:
            print(f"  - {f}")
        return 1

    print("\n[use-cases] all row-count assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
