"""Unit tests for ClickHouseBackend. No live DB needed for these — pure dialect helpers.

The full sink/source integration runs in test_sink_clickhouse.py and gates on
$CHUNKSHOP_TEST_DSN_CH being set.
"""
import pytest

pytest.importorskip("clickhouse_connect")

import json
import numpy as np

from chunkshop.backends.base import ColSpec
from chunkshop.backends.clickhouse import ClickHouseBackend, _parse_clickhouse_dsn


@pytest.fixture
def be():
    return ClickHouseBackend(dsn_env="DUMMY_DSN_NOT_USED")


def test_name_and_supports_upsert(be):
    assert be.name == "clickhouse"
    # CH is append-only by design — supports_upsert must be False
    assert be.supports_upsert is False


def test_quote_ident_uses_backticks(be):
    assert be.quote_ident("my_table") == "`my_table`"


def test_quote_ident_escapes_embedded_backtick(be):
    assert be.quote_ident("weird`name") == "`weird``name`"


def test_fq_table(be):
    assert be.fq_table("chunkshop", "chunks") == "`chunkshop`.`chunks`"


def test_vector_type_ddl_is_array_float32(be):
    # Dim is encoded in the vector_similarity index spec, not the column type
    assert be.vector_type_ddl(384) == "Array(Float32)"
    assert be.vector_type_ddl(768) == "Array(Float32)"


def test_json_type_ddl_is_string(be):
    # CH has experimental JSON type but String + JSONExtract* is the stable path
    assert be.json_type_ddl() == "String"


def test_tags_array_type_ddl(be):
    assert be.tags_array_type_ddl() == "Array(String)"


def test_text_pk_type_ddl(be):
    assert be.text_pk_type_ddl() == "String"


def test_timestamp_now_default_ddl(be):
    out = be.timestamp_now_default_ddl()
    assert "DateTime64" in out
    assert "now64" in out.lower()


def test_vector_literal_returns_python_list(be):
    arr = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = be.vector_literal(arr)
    assert isinstance(out, list)
    assert len(out) == 3
    assert abs(out[0] - 0.1) < 1e-5


def test_tags_literal_passthrough(be):
    # Native Array(String) — clickhouse-connect adapts list[str] directly
    assert be.tags_literal(["a", "b"]) == ["a", "b"]


def test_json_literal_serializes(be):
    out = be.json_literal({"a": 1, "b": [2, 3]})
    assert json.loads(out) == {"a": 1, "b": [2, 3]}


def test_json_path_simple(be):
    # CH uses JSONExtractString with positional path segments
    assert be.json_path_sql("metadata", "lang") == "JSONExtractString(metadata, 'lang')"


def test_json_path_nested(be):
    out = be.json_path_sql("metadata", "entities.ORG")
    assert out == "JSONExtractString(metadata, 'entities', 'ORG')"


def test_upsert_clause_always_empty(be):
    # CH is append-only — upsert_clause always returns "" regardless of args
    assert be.upsert_clause(["id"], ["a", "b"]) == ""
    assert be.upsert_clause(["id"], []) == ""
    assert be.upsert_clause([], []) == ""


def test_create_database_sql(be):
    assert be.create_database_sql("foo") == "CREATE DATABASE IF NOT EXISTS `foo`"


def test_add_column_if_not_exists_sql(be):
    out = be.add_column_if_not_exists_sql("`db`.`tbl`", "newcol", "String")
    assert "ALTER TABLE `db`.`tbl`" in out
    assert "ADD COLUMN IF NOT EXISTS `newcol` String" in out


def test_drop_table_sql_uses_sync(be):
    # SYNC blocks until the drop completes — required for overwrite mode
    # so a subsequent CREATE doesn't race.
    out = be.drop_table_sql("`db`.`tbl`")
    assert "DROP TABLE IF EXISTS" in out
    assert "SYNC" in out


def test_emit_chunks_table_ddl_includes_engine_and_order_by(be):
    cols = [
        ColSpec("id", "String", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "String", nullable=False),
        ColSpec("seq_num", "Int32", nullable=False),
        ColSpec("embedding", "Array(Float32)", nullable=False),
        ColSpec("created_at", "DateTime64(6)", nullable=False, default="now64()"),
    ]
    out = be.emit_chunks_table_ddl(fq="`db`.`chunks`", cols=cols, hnsw=True, dim=384)
    assert len(out) == 1
    sql = out[0]
    assert sql.startswith("CREATE TABLE IF NOT EXISTS `db`.`chunks`")
    assert "ENGINE = MergeTree() ORDER BY (`id`)" in sql
    # CH 24.10 vector_similarity takes 2 args (type, distance); dim is inferred from data
    assert "INDEX vec_idx embedding TYPE vector_similarity('hnsw', 'cosineDistance')" in sql
    # NOTE: no bloom_filter index — CH 24.10.4 bug with combined indexes
    # Non-default columns are NOT marked NOT NULL — CH uses Nullable(T) for nullability,
    # which we don't use here.
    assert "NOT NULL" not in sql


def test_emit_chunks_table_ddl_no_hnsw(be):
    cols = [
        ColSpec("id", "String", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "String", nullable=False),
        ColSpec("seq_num", "Int32", nullable=False),
        ColSpec("embedding", "Array(Float32)", nullable=False),
    ]
    out = be.emit_chunks_table_ddl(fq="`x`.`y`", cols=cols, hnsw=False, dim=384)
    assert "vector_similarity" not in out[0]


def test_emit_chunks_table_ddl_custom_engine(be):
    cols = [
        ColSpec("id", "String", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "String", nullable=False),
        ColSpec("seq_num", "Int32", nullable=False),
        ColSpec("embedding", "Array(Float32)", nullable=False),
        ColSpec("created_at", "DateTime64(6)", nullable=False),
    ]
    out = be.emit_chunks_table_ddl(
        fq="`x`.`y`", cols=cols, hnsw=False, dim=384,
        engine="ReplacingMergeTree(created_at) ORDER BY (id)",
    )
    assert "ENGINE = ReplacingMergeTree(created_at)" in out[0]


def test_dsn_parser_clickhouse_scheme():
    kwargs = _parse_clickhouse_dsn("clickhouse://default:chpw@localhost:8124/mydb")
    assert kwargs["host"] == "localhost"
    assert kwargs["port"] == 8124
    assert kwargs["username"] == "default"
    assert kwargs["password"] == "chpw"
    assert kwargs["database"] == "mydb"
    assert kwargs["secure"] is False


def test_dsn_parser_https_scheme():
    kwargs = _parse_clickhouse_dsn("https://user:pw@cloud.example:8443/x")
    assert kwargs["secure"] is True
    assert kwargs["port"] == 8443


def test_dsn_parser_rejects_bad_scheme():
    with pytest.raises(ValueError, match="ClickHouse"):
        _parse_clickhouse_dsn("postgresql://x:y@h:5432/db")
