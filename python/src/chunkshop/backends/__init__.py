"""Backend layer: connection lifecycle + dialect helpers per database backend."""
from chunkshop.backends.base import Backend, ColSpec
from chunkshop.backends.postgres import PostgresBackend


def load_backend(name: str, dsn_env: str) -> Backend:
    """Factory: return the Backend impl for the given name."""
    if name == "postgres":
        return PostgresBackend(dsn_env=dsn_env)
    # Future: "sqlite" (Phase 4), "mariadb" (Phase 6); "clickhouse" out of scope
    raise ValueError(f"unknown backend: {name!r}")


__all__ = ["Backend", "ColSpec", "PostgresBackend", "load_backend"]
