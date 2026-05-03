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
