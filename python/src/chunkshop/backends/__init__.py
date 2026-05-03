"""Backend layer: connection lifecycle + dialect helpers per database backend."""
from chunkshop.backends.base import Backend, ColSpec
from chunkshop.backends.postgres import PostgresBackend


def load_backend(name: str, dsn_env: str) -> Backend:
    if name == "postgres":
        return PostgresBackend(dsn_env=dsn_env)
    if name == "sqlite":
        from chunkshop.backends.sqlite import SQLiteBackend
        return SQLiteBackend(dsn_env=dsn_env)
    # Future: "mariadb" added in Phase 6; "clickhouse" out of scope this plan.
    raise ValueError(f"unknown backend: {name!r}")


__all__ = ["Backend", "ColSpec", "PostgresBackend", "load_backend"]
