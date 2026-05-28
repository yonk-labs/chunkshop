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


def test_write_edges_round_trip_persists_edge_kind(schema: str) -> None:
    """write_edges persists edge_kind; SELECT round-trips identical values."""
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract("def foo():\n    pass\n", language="python", source_path="a.py")
    ext.extract("def bar():\n    foo()\n", language="python", source_path="b.py")

    n = write_edges(ext, dsn=DSN, schema=schema, project_id="rt")
    assert n >= 1

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql
        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        cur.execute(
            sql.SQL("SELECT edge_type, edge_kind FROM {fq} ORDER BY src_fqn, dst_fqn").format(fq=fq)
        )
        rows = cur.fetchall()
        assert len(rows) >= 1
        for edge_type, edge_kind in rows:
            assert edge_type == "CALLS"
            assert edge_kind == "calls"


def test_write_edges_on_conflict_updates_edge_kind(schema: str) -> None:
    """ON CONFLICT DO UPDATE flips edge_kind to EXCLUDED.edge_kind.

    Hand-inserts a row with edge_kind='references' (the DEFAULT, but a
    deliberately-wrong value for a CALLS edge). Then calls write_edges,
    which generates an edge with the SAME primary key but edge_kind='calls'.
    Asserts the existing row's edge_kind was overwritten to 'calls' — proving
    the `SET edge_kind = EXCLUDED.edge_kind` line in the ON CONFLICT clause
    is actually wired.
    """
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    # Run the extractor once to discover the exact PK tuple write_edges will
    # produce, so we can pre-seed a conflicting row with the wrong edge_kind.
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract("def foo():\n    pass\n", language="python", source_path="a.py")
    ext.extract("def bar():\n    foo()\n", language="python", source_path="b.py")
    edges = ext.finalize(project_id="rt")
    assert len(edges) >= 1, "Need at least one edge to test conflict"
    target = edges[0]
    assert target["edge_kind"] == "calls"

    # Pre-seed the conflicting row with edge_kind='references' (wrong value
    # for a CALLS edge — but valid against the CHECK).
    from psycopg import sql
    fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "INSERT INTO {fq} "
                "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                " dst_node_id, confidence, evidence, edge_kind) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"
            ).format(fq=fq),
            (
                "rt",
                target["edge_type"],
                target["src_fqn"],
                target["dst_fqn"],
                target["src_node_id"],
                target["dst_node_id"],
                0.1,
                "{}",
                "references",  # deliberately wrong
            ),
        )
        conn.commit()

    # Sanity: the pre-seed worked.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT edge_kind FROM {fq} "
                "WHERE project_id = %s AND edge_type = %s "
                "  AND src_node_id = %s AND dst_node_id = %s"
            ).format(fq=fq),
            ("rt", target["edge_type"], target["src_node_id"], target["dst_node_id"]),
        )
        assert cur.fetchone()[0] == "references"

    # Now call write_edges with a fresh extractor producing the same edges.
    # The PK collides → ON CONFLICT DO UPDATE → edge_kind should flip to 'calls'.
    ext2 = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext2.extract("def foo():\n    pass\n", language="python", source_path="a.py")
    ext2.extract("def bar():\n    foo()\n", language="python", source_path="b.py")
    write_edges(ext2, dsn=DSN, schema=schema, project_id="rt")

    # The pre-seeded row's edge_kind must have been overwritten.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT edge_kind FROM {fq} "
                "WHERE project_id = %s AND edge_type = %s "
                "  AND src_node_id = %s AND dst_node_id = %s"
            ).format(fq=fq),
            ("rt", target["edge_type"], target["src_node_id"], target["dst_node_id"]),
        )
        assert cur.fetchone()[0] == "calls", (
            "ON CONFLICT DO UPDATE did not flip edge_kind — "
            "SET edge_kind = EXCLUDED.edge_kind may be missing"
        )
