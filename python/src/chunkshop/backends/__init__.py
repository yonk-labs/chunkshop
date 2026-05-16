"""Backend layer: connection lifecycle + dialect helpers per database backend."""
from chunkshop.backends.base import Backend, ColSpec
from chunkshop.backends.postgres import PostgresBackend


def load_backend(
    name: str, dsn: str | None = None, *, dsn_env: str | None = None
) -> Backend:
    # `dsn` is the resolved connection string (preferred). `dsn_env` is the
    # legacy env-var-name kwarg, kept so older callers keep working unchanged.
    if name == "postgres":
        return PostgresBackend(dsn, dsn_env=dsn_env)
    if name == "sqlite":
        from chunkshop.backends.sqlite import SQLiteBackend
        return SQLiteBackend(dsn, dsn_env=dsn_env)
    if name == "mariadb":
        from chunkshop.backends.mariadb import MariaDBBackend
        return MariaDBBackend(dsn, dsn_env=dsn_env)
    if name == "clickhouse":
        from chunkshop.backends.clickhouse import ClickHouseBackend
        return ClickHouseBackend(dsn, dsn_env=dsn_env)
    raise ValueError(f"unknown backend: {name!r}")


__all__ = ["Backend", "ColSpec", "PostgresBackend", "load_backend"]
