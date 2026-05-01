import os
import pytest
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
