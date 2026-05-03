"""Sink layer: per-backend writers for chunkshop's chunks table."""
from chunkshop.backends import load_backend
from chunkshop.sinks.base import Sink
from chunkshop.sinks.pg import PgSink


def load_sink(cfg, embed_dim: int) -> Sink:
    """Factory: dispatch on cfg.type, attach matching Backend, return Sink."""
    if cfg.type == "postgres":
        backend = load_backend(name="postgres", dsn_env=cfg.dsn_env)
        return PgSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "sqlite":
        from chunkshop.sinks.sqlite import SqliteSink
        backend = load_backend(name="sqlite", dsn_env=cfg.dsn_env)
        return SqliteSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    # Future: "mariadb" added in Phase 7; "clickhouse" out of scope this plan
    raise ValueError(f"unknown target type: {cfg.type!r}")


__all__ = ["Sink", "PgSink", "load_sink"]
