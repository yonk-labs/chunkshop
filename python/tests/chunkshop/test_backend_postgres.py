import os
import json
import pytest
import numpy as np
from chunkshop.backends.postgres import PostgresBackend


@pytest.fixture
def be():
    return PostgresBackend(dsn_env="DUMMY_DSN_NOT_USED_HERE")


def test_name_and_supports_upsert(be):
    assert be.name == "postgres"
    assert be.supports_upsert is True


def test_quote_ident_simple(be):
    assert be.quote_ident("my_table") == '"my_table"'


def test_quote_ident_escapes_embedded_double_quote(be):
    # Postgres identifier escaping: " becomes ""
    assert be.quote_ident('weird"name') == '"weird""name"'


def test_fq_table_joins_schema_and_table(be):
    assert be.fq_table("chunkshop", "my_chunks") == '"chunkshop"."my_chunks"'


def test_connect_reads_from_env(monkeypatch):
    monkeypatch.setenv("PG_TEST_DSN", "postgresql://nosuchhost:1/x")
    be = PostgresBackend(dsn_env="PG_TEST_DSN")
    # Don't actually connect — just check the DSN was wired. The full connect
    # path is exercised by the sink integration tests when $CHUNKSHOP_TEST_DSN
    # is set.
    assert be._dsn == "postgresql://nosuchhost:1/x"


def test_vector_type_ddl(be):
    assert be.vector_type_ddl(384) == "vector(384)"


def test_json_type_ddl(be):
    assert be.json_type_ddl() == "jsonb"


def test_tags_array_type_ddl(be):
    assert be.tags_array_type_ddl() == "text[]"


def test_text_pk_type_ddl(be):
    assert be.text_pk_type_ddl() == "text"


def test_timestamp_now_default_ddl(be):
    assert "now()" in be.timestamp_now_default_ddl().lower()


def test_vector_literal_format(be):
    arr = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = be.vector_literal(arr)
    assert out.startswith("[") and out.endswith("]")
    assert "0.1" in out and "0.2" in out and "0.3" in out


def test_tags_literal_passthrough(be):
    assert be.tags_literal(["a", "b"]) == ["a", "b"]


def test_json_literal_serializes(be):
    out = be.json_literal({"a": 1, "b": [2, 3]})
    assert json.loads(out) == {"a": 1, "b": [2, 3]}


def test_json_path_sql_simple_key(be):
    assert be.json_path_sql("metadata", "lang") == "metadata->>'lang'"


def test_json_path_sql_nested(be):
    assert be.json_path_sql("metadata", "entities.ORG") == "metadata->'entities'->>'ORG'"


def test_create_database_sql_uses_create_schema(be):
    out = be.create_database_sql("chunkshop_test")
    assert "CREATE SCHEMA IF NOT EXISTS" in out
    assert '"chunkshop_test"' in out


def test_add_column_if_not_exists_sql(be):
    out = be.add_column_if_not_exists_sql('"db"."tbl"', "newcol", "text")
    assert "ALTER TABLE" in out
    assert "ADD COLUMN IF NOT EXISTS" in out
    assert '"newcol"' in out
    assert "text" in out


def test_drop_table_sql(be):
    out = be.drop_table_sql('"db"."tbl"')
    assert out.startswith("DROP TABLE")
    assert '"db"."tbl"' in out


def test_upsert_clause_pg_form(be):
    out = be.upsert_clause(["id"], ["content", "metadata"])
    assert "ON CONFLICT" in out and '("id")' in out
    assert 'DO UPDATE SET' in out
    assert '"content" = EXCLUDED."content"' in out
    assert '"metadata" = EXCLUDED."metadata"' in out


def test_upsert_clause_empty_update_cols(be):
    out = be.upsert_clause(["id"], [])
    assert "DO NOTHING" in out
