import pytest
from chunkshop.backends import load_backend


def test_load_backend_postgres():
    be = load_backend(name="postgres", dsn_env="DUMMY_DSN")
    assert be.name == "postgres"


def test_load_backend_unknown():
    with pytest.raises(ValueError, match="unknown backend"):
        load_backend(name="oracle", dsn_env="X")


def test_load_backend_sqlite(monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", ":memory:")
    from chunkshop.backends import load_backend
    be = load_backend(name="sqlite", dsn_env="SQLITE_PATH")
    assert be.name == "sqlite"


def test_load_backend_mariadb():
    pytest.importorskip("pymysql")
    from chunkshop.backends import load_backend
    be = load_backend(name="mariadb", dsn_env="DUMMY")
    assert be.name == "mariadb"
