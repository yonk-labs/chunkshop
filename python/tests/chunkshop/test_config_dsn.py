"""Direct `dsn` field: precedence over `dsn_env` + ${VAR} interpolation (0.4.3)."""
import pytest

from chunkshop.config import (
    ClickhouseTableSource,
    MariaDbTableSource,
    PgTableSource,
    TargetConfig,
)


def _target(**kw):
    base = dict(type="postgres", database="chunkshop", table="my_chunks", mode="overwrite")
    base.update(kw)
    return TargetConfig(**base)


def test_dsn_env_still_works_when_dsn_absent(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://u:p@h/db")
    cfg = _target(dsn_env="PG_DSN")
    assert cfg.resolve_dsn() == "postgresql://u:p@h/db"


def test_dsn_takes_precedence_over_dsn_env(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://env-wins@h/db")
    cfg = _target(dsn="postgresql://literal-wins@h/db", dsn_env="PG_DSN")
    assert cfg.resolve_dsn() == "postgresql://literal-wins@h/db"


def test_dsn_interpolates_braced_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    cfg = _target(dsn="${DATABASE_URL}")
    assert cfg.resolve_dsn() == "postgresql://u:p@h/db"


def test_dsn_literal_with_dollar_in_password_not_mangled():
    # Bare `$` (not ${...}) must be left untouched — DSN passwords contain it.
    cfg = _target(dsn="postgresql://u:pa$$word@h/db")
    assert cfg.resolve_dsn() == "postgresql://u:pa$$word@h/db"


def test_dsn_missing_interpolation_var_raises(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    cfg = _target(dsn="${NOPE}")
    with pytest.raises(ValueError, match="NOPE"):
        cfg.resolve_dsn()


def test_dsn_env_missing_raises_keyerror(monkeypatch):
    monkeypatch.delenv("ABSENT_DSN", raising=False)
    cfg = _target(dsn_env="ABSENT_DSN")
    with pytest.raises(KeyError):
        cfg.resolve_dsn()


def test_neither_dsn_nor_dsn_env_is_a_config_error():
    with pytest.raises(Exception, match="dsn"):
        _target()


def test_backcompat_source_with_dsn_env_constructs_without_env(monkeypatch):
    # Pre-0.4.3 contract: building a *_table source with only `dsn_env` must
    # NOT touch the environment — the env var is read lazily at connect().
    from chunkshop.sources import load_source

    monkeypatch.delenv("UNSET_AT_BUILD", raising=False)
    cfg = ClickhouseTableSource(
        type="clickhouse_table", dsn_env="UNSET_AT_BUILD", database="my_app",
        table="documents", id_column="id", content_column="body",
    )
    src = load_source(cfg)  # must not raise even though env var is unset
    assert type(src).__name__ == "ClickhouseTableSource"


def test_backcompat_backend_dsn_env_kwarg_still_works(monkeypatch):
    # Pre-0.4.3 API: backends + load_backend accept dsn_env= and snapshot it.
    from chunkshop.backends import load_backend
    from chunkshop.backends.postgres import PostgresBackend

    monkeypatch.setenv("PG_TEST_DSN", "postgresql://nosuchhost:1/x")
    assert PostgresBackend(dsn_env="PG_TEST_DSN")._dsn == "postgresql://nosuchhost:1/x"
    be = load_backend(name="postgres", dsn_env="PG_TEST_DSN")
    assert be._dsn == "postgresql://nosuchhost:1/x"


def test_resolve_dsn_works_on_sources(monkeypatch):
    monkeypatch.setenv("SRC_DSN", "postgresql://u:p@h/db")
    s = PgTableSource(
        type="pg_table", dsn_env="SRC_DSN", database="app",
        table="docs", id_column="id", content_column="body",
    )
    assert s.resolve_dsn() == "postgresql://u:p@h/db"

    m = MariaDbTableSource(
        type="mariadb_table", dsn="mysql://root:pw@h:3307/db", database="app",
        table="docs", id_column="id", content_column="body",
    )
    assert m.resolve_dsn() == "mysql://root:pw@h:3307/db"
