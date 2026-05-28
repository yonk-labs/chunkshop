"""Live-PG tests for the CS-5 provenance + provenance_metadata columns.

Skips cleanly when CHUNKSHOP_TEST_DSN isn't reachable. Each test creates
+ drops its own schema so tests don't leak state.
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

    name = f"chunkshop_cs5_{uuid.uuid4().hex[:8]}"
    yield name
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def test_schema_includes_provenance_columns(schema: str) -> None:
    """write_edges_schema creates code_edges with both new columns."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'code_edges' "
            "ORDER BY column_name",
            (schema,),
        )
        cols = {row[0]: row for row in cur.fetchall()}

        # New CS-5 columns.
        assert "provenance" in cols
        assert cols["provenance"][1] == "text"
        assert cols["provenance"][2] == "NO"  # NOT NULL
        assert cols["provenance"][3] is not None  # has DEFAULT

        assert "provenance_metadata" in cols
        assert cols["provenance_metadata"][1] == "jsonb"
        assert cols["provenance_metadata"][2] == "NO"  # NOT NULL
        assert cols["provenance_metadata"][3] is not None  # has DEFAULT


def test_provenance_check_constraint_rejects_invalid_value(schema: str) -> None:
    """CHECK constraint refuses values outside {ast, scip, heuristic}."""
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
                    " dst_node_id, confidence, evidence, edge_kind, provenance) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)"
                ).format(fq=fq),
                ("p", "CALLS", "a", "b", "id_a", "id_b", 0.9, "{}", "calls", "bogus_provenance"),
            )


def test_provenance_check_accepts_all_three_values(schema: str) -> None:
    """All 3 PROVENANCES values satisfy the CHECK constraint."""
    import psycopg

    from chunkshop.extractors.code_relationships import PROVENANCES, write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql

        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        for i, prov in enumerate(PROVENANCES):
            cur.execute(
                sql.SQL(
                    "INSERT INTO {fq} "
                    "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                    " dst_node_id, confidence, evidence, edge_kind, provenance) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)"
                ).format(fq=fq),
                ("p", "CALLS", f"a{i}", f"b{i}", f"id_a{i}", f"id_b{i}", 0.9, "{}", "calls", prov),
            )
        conn.commit()
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {fq}").format(fq=fq))
        assert cur.fetchone()[0] == 3


def test_schema_preserves_cs2_columns_unchanged(schema: str) -> None:
    """SC-004 regression guard: edge_type + edge_kind columns are byte-identical to post-CS-2."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'code_edges' "
            "AND column_name IN ('edge_type', 'edge_kind')",
            (schema,),
        )
        cols = {row[0]: row for row in cur.fetchall()}

        # edge_type (CS-2 legacy): text, NOT NULL.
        assert cols["edge_type"][1] == "text"
        assert cols["edge_type"][2] == "NO"
        # edge_kind (CS-2 typed): text, NOT NULL.
        assert cols["edge_kind"][1] == "text"
        assert cols["edge_kind"][2] == "NO"

        # PK still includes edge_type (not edge_kind, not provenance).
        cur.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = %s::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum)",
            (f"{schema}.code_edges",),
        )
        pk_cols = [r[0] for r in cur.fetchall()]
        assert pk_cols == ["project_id", "edge_type", "src_node_id", "dst_node_id"]
