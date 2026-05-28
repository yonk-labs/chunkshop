"""Live-PG tests for the CS-2 typed edge_kind column.

Skips cleanly when CHUNKSHOP_TEST_DSN isn't reachable so the suite stays
green for contributors without docker-compose.test.yaml running. Each
test creates + drops its own schema so the tests don't leak state.
"""
from __future__ import annotations

import os
import uuid

import pytest

DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/chunkshop_test",
)


def _pg_reachable(dsn: str) -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(DSN),
    reason="CHUNKSHOP_TEST_DSN not reachable; bring up docker-compose.test.yaml",
)


@pytest.fixture
def schema(request):
    """One throwaway schema per test, dropped at teardown."""
    import psycopg
    from psycopg import sql

    name = f"chunkshop_cs2_{uuid.uuid4().hex[:8]}"
    yield name
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def test_schema_includes_edge_kind_column_with_check_constraint(schema: str) -> None:
    """write_edges_schema creates code_edges with edge_kind + 12-value CHECK."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        # Column exists, correct type, NOT NULL, has a DEFAULT.
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'code_edges' "
            "ORDER BY column_name",
            (schema,),
        )
        cols = {row[0]: row for row in cur.fetchall()}
        assert "edge_kind" in cols
        assert cols["edge_kind"][1] == "text"
        assert cols["edge_kind"][2] == "NO"
        assert cols["edge_kind"][3] is not None  # has DEFAULT

        # Legacy edge_type column UNTOUCHED — SC-003 regression guard.
        assert "edge_type" in cols
        assert cols["edge_type"][1] == "text"
        assert cols["edge_type"][2] == "NO"


def test_edge_kind_check_constraint_rejects_invalid_value(schema: str) -> None:
    """CHECK constraint refuses non-codegraph values."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql, errors as pg_errors

        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        with pytest.raises(pg_errors.CheckViolation):
            cur.execute(
                sql.SQL(
                    "INSERT INTO {fq} "
                    "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                    " dst_node_id, confidence, evidence, edge_kind) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"
                ).format(fq=fq),
                ("p", "CALLS", "a", "b", "id_a", "id_b", 0.9, "{}", "bogus_kind"),
            )


def test_edge_kind_check_accepts_all_12_codegraph_values(schema: str) -> None:
    """Every value in EDGE_KINDS satisfies the CHECK constraint."""
    import psycopg

    from chunkshop.extractors.code_relationships import EDGE_KINDS, write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql

        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        for i, kind in enumerate(EDGE_KINDS):
            cur.execute(
                sql.SQL(
                    "INSERT INTO {fq} "
                    "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                    " dst_node_id, confidence, evidence, edge_kind) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"
                ).format(fq=fq),
                ("p", "CALLS", f"a{i}", f"b{i}", f"id_a{i}", f"id_b{i}", 0.9, "{}", kind),
            )
        conn.commit()
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {fq}").format(fq=fq)
        )
        assert cur.fetchone()[0] == 12
