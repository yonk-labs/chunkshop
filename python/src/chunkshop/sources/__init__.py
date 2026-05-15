"""Source registry."""
from chunkshop.config import (
    FilesSource as FilesCfg,
    InlineSource as InlineCfg,
    JsonCorpusSource as JsonCfg,
    PgTableSource as PgCfg,
    SqliteTableSource as SqliteCfg,
    MariaDbTableSource as MariaDbCfg,
    ClickhouseTableSource as ChCfg,
    HttpSource as HttpCfg,
    S3Source as S3Cfg,
    SourceConfig,
)
from chunkshop.sources.base import Document, Source
from chunkshop.sources.files import FilesSource
from chunkshop.sources.json_corpus import JsonCorpusSource
from chunkshop.sources.pg_table import PgTableSource
from chunkshop.sources.sqlite_table import SqliteTableSource
from chunkshop.sources.mariadb_table import MariaDbTableSource
from chunkshop.sources.clickhouse_table import ClickhouseTableSource
from chunkshop.sources.http import HttpSource
from chunkshop.sources.s3 import S3Source


def load_source(cfg: SourceConfig) -> Source:
    if isinstance(cfg, FilesCfg):
        return FilesSource(cfg)
    if isinstance(cfg, JsonCfg):
        return JsonCorpusSource(cfg)
    if isinstance(cfg, PgCfg):
        return PgTableSource(cfg)
    if isinstance(cfg, SqliteCfg):
        return SqliteTableSource(cfg)
    if isinstance(cfg, MariaDbCfg):
        return MariaDbTableSource(cfg)
    if isinstance(cfg, ChCfg):
        return ClickhouseTableSource(cfg)
    if isinstance(cfg, HttpCfg):
        return HttpSource(cfg)
    if isinstance(cfg, S3Cfg):
        return S3Source(cfg)
    if isinstance(cfg, InlineCfg):
        raise RuntimeError(
            "inline source has no auto-iterator: drive ingest from your app "
            "with chunkshop.Pipeline.from_yaml(...).ingest_text(doc_id, text, metadata). "
            "See docs/incremental.md (Pattern F) and docs/samples/inline-mode/."
        )
    raise ValueError(f"unknown source type: {type(cfg).__name__}")


__all__ = ["Document", "Source", "load_source"]
