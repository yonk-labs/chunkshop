"""Sink layer: per-backend writers for chunkshop's chunks table."""
from chunkshop.backends import load_backend
from chunkshop.sinks.base import Sink
from chunkshop.sinks.pg import PgSink


def load_sink(cfg, embed_dim: int) -> Sink:
    if cfg.type == "postgres":
        backend = load_backend(name="postgres", dsn_env=cfg.dsn_env)
        return PgSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "sqlite":
        from chunkshop.sinks.sqlite import SqliteSink
        backend = load_backend(name="sqlite", dsn_env=cfg.dsn_env)
        return SqliteSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "mariadb":
        from chunkshop.sinks.mariadb import MariaDbSink
        backend = load_backend(name="mariadb", dsn_env=cfg.dsn_env)
        return MariaDbSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    raise ValueError(f"unknown target type: {cfg.type!r}")


__all__ = ["Sink", "PgSink", "load_sink"]
