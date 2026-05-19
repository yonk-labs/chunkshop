"""Sink layer: per-backend writers for chunkshop's chunks table."""
from chunkshop.backends import load_backend
from chunkshop.sinks.base import Sink
from chunkshop.sinks.pg import PgSink


def load_sink(cfg, embed_dim: int) -> Sink:
    dsn_kwargs = cfg.backend_dsn_kwargs()
    if cfg.type == "postgres":
        backend = load_backend(name="postgres", **dsn_kwargs)
        from chunkshop.config import MemoryConfig
        if isinstance(getattr(cfg, "memory", None), MemoryConfig):
            from chunkshop.sinks.memory_pg import MemorySink
            return MemorySink(cfg=cfg, backend=backend, embed_dim=embed_dim)
        return PgSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "sqlite":
        from chunkshop.sinks.sqlite import SqliteSink
        backend = load_backend(name="sqlite", **dsn_kwargs)
        return SqliteSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "mariadb":
        from chunkshop.sinks.mariadb import MariaDbSink
        backend = load_backend(name="mariadb", **dsn_kwargs)
        return MariaDbSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "clickhouse":
        from chunkshop.sinks.clickhouse import ClickHouseSink
        backend = load_backend(name="clickhouse", **dsn_kwargs)
        return ClickHouseSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    raise ValueError(f"unknown target type: {cfg.type!r}")


__all__ = ["Sink", "PgSink", "MemorySink", "load_sink"]
