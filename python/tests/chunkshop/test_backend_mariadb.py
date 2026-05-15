import pytest

pytest.importorskip("pymysql")

from chunkshop.backends.mariadb import MariaDBBackend


@pytest.fixture
def be():
    return MariaDBBackend(dsn_env="DUMMY_DSN")


def test_name_and_supports_upsert(be):
    assert be.name == "mariadb"
    assert be.supports_upsert is True


def test_quote_ident_uses_backticks(be):
    assert be.quote_ident("my_table") == "`my_table`"


def test_quote_ident_escapes_embedded_backtick(be):
    assert be.quote_ident("weird`name") == "`weird``name`"


def test_fq_table(be):
    assert be.fq_table("chunkshop", "chunks") == "`chunkshop`.`chunks`"


import json
import numpy as np


def test_vector_type_ddl(be):
    assert be.vector_type_ddl(384) == "VECTOR(384)"


def test_json_type_ddl(be):
    assert be.json_type_ddl() == "JSON"


def test_tags_array_type_ddl(be):
    assert be.tags_array_type_ddl() == "JSON"


def test_text_pk_type_ddl(be):
    assert be.text_pk_type_ddl() == "VARCHAR(255)"


def test_timestamp_now_default_ddl(be):
    out = be.timestamp_now_default_ddl()
    assert "TIMESTAMP" in out.upper()
    assert "CURRENT_TIMESTAMP" in out.upper() or "NOW()" in out.upper()


def test_vector_literal_uses_vec_fromtext(be):
    arr = np.array([0.1, 0.2], dtype=np.float32)
    out = be.vector_literal(arr)
    assert "VEC_FromText" in out
    assert "0.1" in out and "0.2" in out


def test_tags_literal_serializes_to_json(be):
    out = be.tags_literal(["a", "b"])
    assert json.loads(out) == ["a", "b"]


def test_json_literal_serializes(be):
    out = be.json_literal({"a": 1})
    assert json.loads(out) == {"a": 1}


def test_json_path_sql_simple(be):
    assert be.json_path_sql("metadata", "lang") == "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.lang'))"


def test_json_path_sql_nested(be):
    out = be.json_path_sql("metadata", "entities.ORG")
    assert out == "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.entities.ORG'))"


from chunkshop.backends.base import ColSpec


def test_create_database_sql(be):
    out = be.create_database_sql("chunkshop_test")
    assert "CREATE DATABASE IF NOT EXISTS" in out
    assert "`chunkshop_test`" in out


def test_add_column_if_not_exists_sql(be):
    out = be.add_column_if_not_exists_sql("`db`.`tbl`", "newcol", "JSON")
    assert "ALTER TABLE" in out
    assert "ADD COLUMN IF NOT EXISTS" in out
    assert "`newcol` JSON" in out


def test_drop_table_sql(be):
    assert be.drop_table_sql("`db`.`tbl`") == "DROP TABLE `db`.`tbl`"


def test_upsert_clause(be):
    out = be.upsert_clause(["id"], ["content", "metadata"])
    assert "ON DUPLICATE KEY UPDATE" in out
    assert "`content` = VALUES(`content`)" in out
    assert "`metadata` = VALUES(`metadata`)" in out


def test_emit_chunks_table_ddl_inline_vector_index(be):
    cols = [
        ColSpec("id", "VARCHAR(255)", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "VARCHAR(255)", nullable=False),
        ColSpec("seq_num", "INT", nullable=False),
        ColSpec("embedding", "VECTOR(384)", nullable=False),
    ]
    out = be.emit_chunks_table_ddl(fq="`db`.`chunks`", cols=cols, hnsw=True, dim=384)
    assert len(out) >= 1
    create = out[0]
    assert create.startswith("CREATE TABLE IF NOT EXISTS")
    assert "VECTOR INDEX" in create or "VECTOR KEY" in create
    assert "`embedding`" in create


def test_emit_chunks_table_ddl_engine_clause(be):
    out = be.emit_chunks_table_ddl(
        fq="`db`.`chunks`",
        cols=[
            ColSpec("id", "VARCHAR(255)", is_primary_key=True, nullable=False),
            ColSpec("doc_id", "VARCHAR(255)", nullable=False),
            ColSpec("seq_num", "INT", nullable=False),
        ],
        hnsw=False,
        dim=384,
        engine="InnoDB",
    )
    assert "ENGINE=InnoDB" in out[0]
