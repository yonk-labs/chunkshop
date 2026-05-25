import json
import os

import numpy as np
import pytest

from chunkshop.chunkers.base import Chunk
from chunkshop.backends.postgres import PostgresBackend
from chunkshop.config import TargetConfig
from chunkshop.sinks import load_sink
from chunkshop.sinks.pg import PgSink


DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"


class _FakeCursor:
    def __init__(self):
        self.calls = []
        self.executemany_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        self.calls.append((stmt, params))

    def executemany(self, stmt, rows):
        self.executemany_calls.append((stmt, rows))


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1


def test_write_document_record_upserts_document_metadata(monkeypatch):
    cfg = TargetConfig(
        type="postgres",
        dsn="postgresql://unused",
        database="chunkshop_docs",
        table="chunks",
        source_tag="scotus",
        documents={
            "enabled": True,
            "table": "documents",
            "store_full_content": True,
            "store_lede_report": True,
            "promote_metadata": [
                {"path": "lede_report.attributes.term.value", "type": "text"},
                {"path": "lede_report.attributes.term", "type": "text"},
            ],
        },
    )
    backend = PostgresBackend(dsn="postgresql://unused")
    conn = _FakeConnection()
    monkeypatch.setattr(backend, "connect", lambda: conn)

    sink = PgSink(cfg, backend, embed_dim=3)
    sink.write_document_record(
        doc_id="2023_snyder",
        title="Snyder v. United States",
        content="Opinion text about the 2023 term.",
        chunk_count=4,
        metadata={
            "uri": "file:///tmp/snyder.md",
            "lede_report": {
                "summary": {"summary": "The Court considered Snyder."},
                "toc": ["Syllabus", "Opinion"],
                "key_facts": ["Term: 2023"],
                "fact_records": [{"predicate": "term", "object": "2023"}],
                "search_text": "Snyder Justice Jackson 2023",
                "attributes": {
                    "term": {"value": "2023"},
                },
            },
        },
    )
    sink.write_document(
        "2023_snyder",
        [
            Chunk(
                doc_id="2023_snyder",
                seq_num=0,
                original_content="chunk text",
                embedded_content="chunk text",
                metadata={},
            )
        ],
        np.array([[1.0, 0.0, 0.0]], dtype=np.float32),
        [["legal"]],
    )

    assert conn.commits == 1
    stmt, params = conn.cursor_obj.calls[0]
    assert 'INSERT INTO "chunkshop_docs"."documents"' in stmt
    assert '"lede_report__attributes__term__value"' in stmt
    assert '"updated_at" = now()' in stmt
    assert 'EXCLUDED."updated_at"' not in stmt
    assert '"source" = EXCLUDED."source"' not in stmt

    assert params[0] == "2023_snyder"
    assert params[1] == "scotus"
    assert params[2] == "Snyder v. United States"
    assert params[3] == "file:///tmp/snyder.md"
    assert params[6] == "Opinion text about the 2023 term."
    assert json.loads(params[9]) == ["Syllabus", "Opinion"]
    assert json.loads(params[10]) == [
        "Term: 2023",
        {"predicate": "term", "object": "2023"},
    ]
    assert json.loads(params[11])["summary"]["summary"] == "The Court considered Snyder."
    assert params[-2] == "2023"
    assert json.loads(params[-1]) == {"value": "2023"}
    assert len(conn.cursor_obj.executemany_calls) == 1


def test_write_document_record_can_omit_full_content_and_lede_report(monkeypatch):
    cfg = TargetConfig(
        type="postgres",
        dsn="postgresql://unused",
        database="chunkshop_docs",
        table="chunks",
        documents={
            "enabled": True,
            "table": "documents",
            "store_full_content": False,
            "store_lede_report": False,
        },
    )
    backend = PostgresBackend(dsn="postgresql://unused")
    conn = _FakeConnection()
    monkeypatch.setattr(backend, "connect", lambda: conn)

    sink = PgSink(cfg, backend, embed_dim=3)
    sink.write_document_record(
        doc_id="doc1",
        title=None,
        content="full text",
        chunk_count=1,
        metadata={"lede_report": {"summary": "compact"}},
    )
    sink.write_document(
        "doc1",
        [Chunk(doc_id="doc1", seq_num=0, original_content="x", embedded_content="x", metadata={})],
        np.array([[1.0, 0.0, 0.0]], dtype=np.float32),
        [[]],
    )

    _stmt, params = conn.cursor_obj.calls[0]
    assert params[6] is None
    assert "lede_report" not in json.loads(params[7])
    assert json.loads(params[11]) == {}


def test_pending_document_record_restored_if_chunk_write_fails(monkeypatch):
    class FailingCursor(_FakeCursor):
        def executemany(self, stmt, rows):
            super().executemany(stmt, rows)
            raise RuntimeError("boom")

    class FailingConnection(_FakeConnection):
        def __init__(self):
            self.cursor_obj = FailingCursor()
            self.commits = 0

    cfg = TargetConfig(
        type="postgres",
        dsn="postgresql://unused",
        database="chunkshop_docs",
        table="chunks",
        documents={"enabled": True, "table": "documents"},
    )
    backend = PostgresBackend(dsn="postgresql://unused")
    conn = FailingConnection()
    monkeypatch.setattr(backend, "connect", lambda: conn)

    sink = PgSink(cfg, backend, embed_dim=3)
    sink.write_document_record(
        doc_id="doc1",
        title=None,
        content="full text",
        chunk_count=1,
        metadata={},
    )
    with pytest.raises(RuntimeError, match="boom"):
        sink.write_document(
            "doc1",
            [Chunk(doc_id="doc1", seq_num=0, original_content="x", embedded_content="x", metadata={})],
            np.array([[1.0, 0.0, 0.0]], dtype=np.float32),
            [[]],
        )

    assert "doc1" in sink._pending_document_records
    assert conn.commits == 0


@pytest.fixture
def ensure_pg():
    try:
        import psycopg
    except Exception as exc:
        pytest.skip(f"psycopg unavailable: {exc}")
    dsn = os.environ.get(DSN_ENV, DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    os.environ[DSN_ENV] = dsn
    yield dsn
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_docs_e2e CASCADE")
        conn.commit()


def _doc_target(**overrides) -> TargetConfig:
    kwargs = {
        "type": "postgres",
        "dsn_env": DSN_ENV,
        "database": "chunkshop_docs_e2e",
        "table": "chunks",
        "mode": "create_if_missing",
        "source_tag": "scotus",
        "hnsw": False,
        "documents": {
            "enabled": True,
            "table": "documents",
            "store_full_content": True,
            "store_lede_report": True,
            "promote_metadata": [
                {"path": "lede_report.attributes.term.value", "type": "text"},
            ],
            "fts": {"enabled": True, "language": "english"},
        },
    }
    kwargs.update(overrides)
    return TargetConfig(**kwargs)


def test_pg_document_table_e2e_create_write_update_delete(ensure_pg):
    import psycopg

    cfg = _doc_target()
    sink = load_sink(cfg, embed_dim=4)
    sink.create_table()

    sink.write_document_record(
        doc_id="case1",
        title="Snyder v. United States",
        content="Full opinion text mentions Justice Jackson and the 2023 term.",
        chunk_count=1,
        metadata={
            "lede_report": {
                "summary": "Justice Jackson is mentioned in a 2023 term case.",
                "toc": ["Syllabus"],
                "key_facts": ["Term: 2023"],
                "search_text": "Justice Jackson 2023 term Snyder",
                "attributes": {"term": {"value": "2023"}},
            }
        },
    )
    sink.write_document(
        "case1",
        [
            Chunk(
                doc_id="case1",
                seq_num=0,
                original_content="Justice Jackson appeared in the opinion.",
                embedded_content="Justice Jackson appeared in the opinion.",
                metadata={"lede_report": {"attributes": {"term": {"value": "2023"}}}},
            )
        ],
        np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32),
        [["justice"]],
    )

    with psycopg.connect(ensure_pg) as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT d.source, d.title, d.full_content, d.lede_summary, '
            'd.lede_toc, d.lede_facts, d.lede_report, d.lede_search_text, '
            'd.lede_report__attributes__term__value, d.chunk_count, c.original_content '
            'FROM chunkshop_docs_e2e.documents d '
            'JOIN chunkshop_docs_e2e.chunks c USING (doc_id)'
        )
        row = cur.fetchone()
        assert row[0] == "scotus"
        assert row[1] == "Snyder v. United States"
        assert "Full opinion text" in row[2]
        assert "Justice Jackson" in row[3]
        assert row[4] == ["Syllabus"]
        assert "Term: 2023" in row[5]
        assert row[6]["attributes"]["term"]["value"] == "2023"
        assert "Snyder" in row[7]
        assert row[8] == "2023"
        assert row[9] == 1
        assert "Justice Jackson appeared" in row[10]

        cur.execute(
            "SELECT doc_id FROM chunkshop_docs_e2e.documents "
            "WHERE search_vector @@ plainto_tsquery('english', 'Jackson Snyder')"
        )
        assert cur.fetchone()[0] == "case1"

    sink.write_document_record(
        doc_id="case1",
        title="Updated title",
        content="Updated full text.",
        chunk_count=1,
        metadata={"lede_report": {"summary": "Updated summary", "attributes": {"term": {"value": "2024"}}}},
    )
    sink.write_document(
        "case1",
        [Chunk(doc_id="case1", seq_num=0, original_content="updated", embedded_content="updated", metadata={})],
        np.array([[0.0, 1.0, 0.0, 0.0]], dtype=np.float32),
        [["updated"]],
    )

    with psycopg.connect(ensure_pg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source, title, lede_summary, lede_report__attributes__term__value "
            "FROM chunkshop_docs_e2e.documents"
        )
        row = cur.fetchone()
        assert row == ("scotus", "Updated title", "Updated summary", "2024")

    assert sink.delete_document("case1") == 1
    with psycopg.connect(ensure_pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunkshop_docs_e2e.documents")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM chunkshop_docs_e2e.chunks")
        assert cur.fetchone()[0] == 0


def test_overwrite_refuses_foreign_document_table_source_tag(ensure_pg):
    import psycopg

    cfg = _doc_target()
    sink = load_sink(cfg, embed_dim=4)
    sink.create_table()
    with psycopg.connect(ensure_pg) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chunkshop_docs_e2e.documents "
            "(doc_id, source, metadata, lede_toc, lede_facts, lede_report, chunk_count) "
            "VALUES ('foreign', 'other_source', '{}'::jsonb, '[]'::jsonb, "
            "'[]'::jsonb, '{}'::jsonb, 0)"
        )
        conn.commit()

    cfg_overwrite = _doc_target(mode="overwrite", source_tag="scotus")
    with pytest.raises(RuntimeError, match="other_source"):
        load_sink(cfg_overwrite, embed_dim=4).create_table()


def test_documents_enabled_rejects_non_postgres_target():
    with pytest.raises(ValueError, match="only for postgres"):
        TargetConfig(
            type="sqlite",
            dsn="file:///tmp/chunkshop.db",
            database="ignored",
            table="chunks",
            documents={"enabled": True},
        )
