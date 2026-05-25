"""Source registry.

Backend-specific sources are imported **lazily** inside `load_source` so
consumers that only need one backend can `pip install chunkshop` (no extras)
and `from chunkshop.sources import load_source` without dragging in
`pymysql` / `sqlite-vec` / `clickhouse-connect` / `boto3`. Closes #7.
Mirrors the lazy pattern already used in `chunkshop.sinks.__init__`.
"""
from chunkshop.config import (
    CommentExtractsSource as CommentExtractsCfg,
    FilesSource as FilesCfg,
    InlineSource as InlineCfg,
    JsonCorpusSource as JsonCfg,
    PgTableSource as PgCfg,
    SqliteTableSource as SqliteCfg,
    MariaDbTableSource as MariaDbCfg,
    ClickhouseTableSource as ChCfg,
    ConnectorSource as ConnectorCfg,
    HttpSource as HttpCfg,
    S3Source as S3Cfg,
    SessionStagingSource as SessionStagingCfg,
    SourceConfig,
)
from chunkshop.sources.base import Document, Source
from chunkshop.sources.session_staging import SessionStagingSource


def load_source(cfg: SourceConfig) -> Source:
    if isinstance(cfg, FilesCfg):
        from chunkshop.sources.files import FilesSource
        return FilesSource(cfg)
    if isinstance(cfg, CommentExtractsCfg):
        from chunkshop.sources.comment_extracts import CommentExtractsSource
        return CommentExtractsSource(cfg)
    if isinstance(cfg, JsonCfg):
        from chunkshop.sources.json_corpus import JsonCorpusSource
        return JsonCorpusSource(cfg)
    if isinstance(cfg, PgCfg):
        from chunkshop.sources.pg_table import PgTableSource
        return PgTableSource(cfg)
    if isinstance(cfg, SqliteCfg):
        from chunkshop.sources.sqlite_table import SqliteTableSource
        return SqliteTableSource(cfg)
    if isinstance(cfg, MariaDbCfg):
        from chunkshop.sources.mariadb_table import MariaDbTableSource
        return MariaDbTableSource(cfg)
    if isinstance(cfg, ChCfg):
        from chunkshop.sources.clickhouse_table import ClickhouseTableSource
        return ClickhouseTableSource(cfg)
    if isinstance(cfg, HttpCfg):
        from chunkshop.sources.http import HttpSource
        return HttpSource(cfg)
    if isinstance(cfg, S3Cfg):
        from chunkshop.sources.s3 import S3Source
        return S3Source(cfg)
    if isinstance(cfg, SessionStagingCfg):
        return SessionStagingSource(cfg)
    if isinstance(cfg, ConnectorCfg):
        from chunkshop.sources.registry import load_connector
        return load_connector(cfg.connector, cfg.config)
    if isinstance(cfg, InlineCfg):
        raise RuntimeError(
            "inline source has no auto-iterator: drive ingest from your app "
            "with chunkshop.Pipeline.from_yaml(...).ingest_text(doc_id, text, metadata). "
            "See docs/incremental.md (Pattern F) and docs/samples/inline-mode/."
        )
    raise ValueError(f"unknown source type: {type(cfg).__name__}")


__all__ = ["Document", "Source", "load_source", "SessionStagingSource"]
