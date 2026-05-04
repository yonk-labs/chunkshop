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
