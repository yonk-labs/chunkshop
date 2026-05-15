import pytest
import sqlite3

pytest.importorskip("sqlite_vec")

from chunkshop.backends.sqlite import SQLiteBackend


@pytest.fixture
def be(monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", ":memory:")
    return SQLiteBackend(dsn_env="SQLITE_PATH")


def test_name_and_supports_upsert(be):
    assert be.name == "sqlite"
    assert be.supports_upsert is True


def test_quote_ident_uses_double_quotes(be):
    assert be.quote_ident("my_table") == '"my_table"'


def test_quote_ident_escapes_embedded_double_quote(be):
    assert be.quote_ident('weird"name') == '"weird""name"'


def test_fq_table_ignores_db_prefix(be):
    # SQLite has no schema concept — the database value from YAML is ignored;
    # fq returns just the bare table identifier.
    assert be.fq_table("anything", "chunks") == '"chunks"'


def test_connect_loads_vec_extension_and_enables_wal(be):
    with be.connect() as conn:
        cur = conn.cursor()
        # vec_version() is provided by sqlite-vec when its extension is loaded
        cur.execute("SELECT vec_version()")
        v = cur.fetchone()[0]
        assert v.startswith("v") or v[0].isdigit()
        cur.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0].lower()
        assert mode in {"wal", "memory"}


import json
import numpy as np


def test_vector_type_ddl(be):
    assert be.vector_type_ddl(384) == "FLOAT[384]"


def test_json_type_ddl(be):
    assert be.json_type_ddl() == "TEXT"


def test_tags_array_type_ddl(be):
    assert be.tags_array_type_ddl() == "TEXT"


def test_text_pk_type_ddl(be):
    assert be.text_pk_type_ddl() == "TEXT"


def test_timestamp_now_default_ddl(be):
    assert "CURRENT_TIMESTAMP" in be.timestamp_now_default_ddl().upper()


def test_vector_literal_returns_json_array(be):
    arr = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = be.vector_literal(arr)
    parsed = json.loads(out)
    assert len(parsed) == 3
    assert abs(parsed[0] - 0.1) < 1e-5


def test_tags_literal_serializes_to_json(be):
    assert json.loads(be.tags_literal(["a", "b"])) == ["a", "b"]


def test_json_literal_serializes(be):
    assert json.loads(be.json_literal({"a": 1})) == {"a": 1}


def test_json_path_simple(be):
    assert be.json_path_sql("metadata", "lang") == "json_extract(metadata,'$.lang')"


def test_json_path_nested(be):
    assert be.json_path_sql("metadata", "entities.ORG") == "json_extract(metadata,'$.entities.ORG')"


def test_create_database_sql_is_noop_comment(be):
    out = be.create_database_sql("anything")
    assert "SELECT 1" in out or out.strip() == ""


def test_drop_table_sql(be):
    assert be.drop_table_sql('"chunks"') == 'DROP TABLE "chunks"'


def test_add_column_if_not_exists_sql(be):
    out = be.add_column_if_not_exists_sql('"chunks"', "newcol", "TEXT")
    assert "ALTER TABLE" in out
    assert '"newcol"' in out


def test_upsert_clause_pg_compat(be):
    out = be.upsert_clause(["id"], ["content", "metadata"])
    assert "ON CONFLICT" in out and '("id")' in out
    assert 'DO UPDATE SET' in out
    assert '"content" = excluded."content"' in out
    assert '"metadata" = excluded."metadata"' in out


def test_upsert_clause_empty_update_cols(be):
    out = be.upsert_clause(["id"], [])
    assert "DO NOTHING" in out


from chunkshop.backends.base import ColSpec


def _canonical_cols_with_embedding():
    return [
        ColSpec("id", "TEXT", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "TEXT", nullable=False),
        ColSpec("seq_num", "INTEGER", nullable=False),
        ColSpec("original_content", "TEXT", nullable=False),
        ColSpec("embedded_content", "TEXT", nullable=False),
        ColSpec("tags", "TEXT", nullable=False, default="'[]'"),
        ColSpec("metadata", "TEXT", nullable=False, default="'{}'"),
        ColSpec("embedding", "FLOAT[384]", nullable=False),
        ColSpec("source", "TEXT"),
        ColSpec("created_at", "TEXT", nullable=False, default="CURRENT_TIMESTAMP"),
    ]


def test_emit_chunks_table_ddl_two_tables(be):
    cols = _canonical_cols_with_embedding()
    out = be.emit_chunks_table_ddl(fq='"chunks"', cols=cols, hnsw=False, dim=384)
    assert len(out) == 3
    create_main = out[0]
    assert "CREATE TABLE IF NOT EXISTS" in create_main
    assert '"embedding"' not in create_main
    assert '"id" TEXT' in create_main
    assert "PRIMARY KEY" in create_main
    create_idx = out[1]
    assert "CREATE INDEX IF NOT EXISTS" in create_idx and "doc_id" in create_idx and "seq_num" in create_idx
    create_vec = out[2]
    assert "CREATE VIRTUAL TABLE IF NOT EXISTS" in create_vec
    assert "USING vec0" in create_vec
    assert "FLOAT[384]" in create_vec


def test_emit_chunks_table_ddl_hnsw_true_logs_no_special_index(be):
    cols = _canonical_cols_with_embedding()
    out = be.emit_chunks_table_ddl(fq='"chunks"', cols=cols, hnsw=True, dim=384)
    assert len(out) == 3


def test_with_create_lock_is_noop(be):
    with be.connect() as conn:
        cur = conn.cursor()
        with be.with_create_lock(cur, "anything"):
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1


def test_table_exists(be):
    with be.connect() as conn:
        cur = conn.cursor()
        assert be.table_exists(cur, "ignored", "no_such_table") is False
        cur.execute('CREATE TABLE "real_one" (id TEXT)')
        assert be.table_exists(cur, "ignored", "real_one") is True


def test_embedding_dim_introspection(be):
    with be.connect() as conn:
        cur = conn.cursor()
        cols = _canonical_cols_with_embedding()
        for stmt in be.emit_chunks_table_ddl(fq='"chunks"', cols=cols, hnsw=False, dim=384):
            if stmt.strip().startswith("CREATE"):
                cur.execute(stmt)
        assert be.embedding_dim(cur, "ignored", "chunks") == 384
        assert be.embedding_dim(cur, "ignored", "no_such") is None
