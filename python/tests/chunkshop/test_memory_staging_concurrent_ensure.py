"""ensure_staging_table must be safe when several cells make the first call at once.

`CREATE SCHEMA / TABLE IF NOT EXISTS` is not race-free in Postgres: concurrent
first calls collide on the catalog's unique indexes unless they are serialized.
"""
from __future__ import annotations

import os
import threading
import uuid

import psycopg
import pytest

from chunkshop.memory.staging import ensure_staging_table

DSN = os.environ.get("CHUNKSHOP_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="CHUNKSHOP_TEST_DSN not set")
def test_concurrent_first_calls_do_not_collide():
    errors: list[BaseException] = []
    for _ in range(8):  # fresh schema each round: the race is only on the first call
        schema = f"chunkshop_review_p1u_{uuid.uuid4().hex[:10]}"
        start = threading.Barrier(12)

        def call():
            try:
                start.wait(timeout=10)
                ensure_staging_table(DSN, table="staging", schema=schema)
            except BaseException as e:  # noqa: BLE001 - collected and asserted below
                errors.append(e)

        threads = [threading.Thread(target=call) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        with psycopg.connect(DSN) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            c.commit()
    assert not errors, f"{len(errors)} concurrent calls failed, first: {errors[0]!r}"
