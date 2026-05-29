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

def test_write_edges_round_trip_persists_provenance_ast(schema: str) -> None:
    """write_edges populates provenance='ast' and provenance_metadata={} on every row."""
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    # Intra-file call (foo + bar in one file) => provenance='ast'. (Cross-file
    # name-resolved edges are now 'heuristic' — covered by a separate test.)
    ext.extract(
        "def foo():\n    pass\n\n\ndef bar():\n    foo()\n",
        language="python",
        source_path="a.py",
    )

    n = write_edges(ext, dsn=DSN, schema=schema, project_id="rt")
    assert n >= 1

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql
        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        cur.execute(
            sql.SQL("SELECT provenance, provenance_metadata FROM {fq}").format(fq=fq)
        )
        rows = cur.fetchall()
        assert len(rows) >= 1
        for provenance, metadata in rows:
            assert provenance == "ast"
            assert metadata == {}


def test_provenance_metadata_round_trips_arbitrary_json(schema: str) -> None:
    """SC-003: provenance_metadata jsonb accepts and round-trips arbitrary JSON."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    payload = {"foo": "bar", "nested": [1, 2], "k": None, "deep": {"a": "b"}}

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql
        from psycopg.types.json import Json
        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        cur.execute(
            sql.SQL(
                "INSERT INTO {fq} "
                "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                " dst_node_id, confidence, evidence, edge_kind, provenance, "
                " provenance_metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)"
            ).format(fq=fq),
            ("p", "CALLS", "a", "b", "id_a", "id_b", 0.9, "{}", "calls", "heuristic", Json(payload)),
        )
        conn.commit()
        cur.execute(sql.SQL("SELECT provenance_metadata FROM {fq}").format(fq=fq))
        round_tripped = cur.fetchone()[0]
        assert round_tripped == payload


def test_write_edges_on_conflict_updates_provenance(schema: str) -> None:
    """ON CONFLICT DO UPDATE flips provenance + provenance_metadata to EXCLUDED values.

    Pre-seeds a conflicting row with deliberately wrong values (provenance='heuristic',
    provenance_metadata={'fake': 'data'}). Then calls write_edges which produces an
    edge with the SAME primary key but provenance='ast' / metadata={}. Asserts the
    existing row was overwritten — proving the SET clause is wired (mirrors CS-2's
    test_write_edges_on_conflict_updates_edge_kind pattern).
    """
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    # Run extractor once to discover the PK tuple write_edges will produce.
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(
        "def foo():\n    pass\n\n\ndef bar():\n    foo()\n",
        language="python",
        source_path="a.py",
    )
    edges = ext.finalize(project_id="rt")
    assert len(edges) >= 1
    target = edges[0]
    assert target["provenance"] == "ast"
    assert target["provenance_metadata"] == {}

    # Pre-seed with wrong values (valid against CHECK; wrong for an AST edge).
    from psycopg import sql
    from psycopg.types.json import Json
    fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "INSERT INTO {fq} "
                "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                " dst_node_id, confidence, evidence, edge_kind, provenance, "
                " provenance_metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)"
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
                "calls",
                "heuristic",  # deliberately wrong
                Json({"fake": "data"}),  # deliberately wrong
            ),
        )
        conn.commit()

    # Sanity-check pre-seed.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT provenance, provenance_metadata FROM {fq} "
                "WHERE project_id = %s AND edge_type = %s "
                "  AND src_node_id = %s AND dst_node_id = %s"
            ).format(fq=fq),
            ("rt", target["edge_type"], target["src_node_id"], target["dst_node_id"]),
        )
        row = cur.fetchone()
        assert row[0] == "heuristic"
        assert row[1] == {"fake": "data"}

    # Now run write_edges — PK collides → ON CONFLICT DO UPDATE → flip to ast/{}.
    ext2 = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext2.extract(
        "def foo():\n    pass\n\n\ndef bar():\n    foo()\n",
        language="python",
        source_path="a.py",
    )
    write_edges(ext2, dsn=DSN, schema=schema, project_id="rt")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT provenance, provenance_metadata FROM {fq} "
                "WHERE project_id = %s AND edge_type = %s "
                "  AND src_node_id = %s AND dst_node_id = %s"
            ).format(fq=fq),
            ("rt", target["edge_type"], target["src_node_id"], target["dst_node_id"]),
        )
        row = cur.fetchone()
        assert row[0] == "ast", (
            "ON CONFLICT DO UPDATE did not flip provenance — "
            "SET provenance = EXCLUDED.provenance may be missing"
        )
        assert row[1] == {}, (
            "ON CONFLICT DO UPDATE did not flip provenance_metadata — "
            "SET provenance_metadata = EXCLUDED.provenance_metadata may be missing"
        )


def test_heuristic_cross_file_edges_persist(schema: str) -> None:
    """Cross-file name-resolved edges round-trip into code_edges as provenance='heuristic'."""
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract("def helper(v):\n    return v * 2\n", language="python", source_path="a.py")
    ext.extract("def caller(v):\n    return helper(v)\n", language="python", source_path="b.py")
    write_edges(ext, dsn=DSN, schema=schema, project_id="test")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT provenance FROM "{schema}".code_edges '
            "WHERE edge_type = 'CALLS' AND dst_fqn LIKE '%%.helper'"
        )
        rows = [r[0] for r in cur.fetchall()]
    assert rows, "no CALLS->helper edge persisted"
    assert all(p == "heuristic" for p in rows)
