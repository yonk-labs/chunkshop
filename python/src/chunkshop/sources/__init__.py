"""Source registry."""
from chunkshop.config import (
    FilesSource as FilesCfg,
    JsonCorpusSource as JsonCfg,
    PgTableSource as PgCfg,
    HttpSource as HttpCfg,
    S3Source as S3Cfg,
    SourceConfig,
)
from chunkshop.sources.base import Document, Source
from chunkshop.sources.files import FilesSource
from chunkshop.sources.json_corpus import JsonCorpusSource
from chunkshop.sources.pg_table import PgTableSource
from chunkshop.sources.http import HttpSource
from chunkshop.sources.s3 import S3Source


def load_source(cfg: SourceConfig) -> Source:
    if isinstance(cfg, FilesCfg):
        return FilesSource(cfg)
    if isinstance(cfg, JsonCfg):
        return JsonCorpusSource(cfg)
    if isinstance(cfg, PgCfg):
        return PgTableSource(cfg)
    if isinstance(cfg, HttpCfg):
        return HttpSource(cfg)
    if isinstance(cfg, S3Cfg):
        return S3Source(cfg)
    raise ValueError(f"unknown source type: {type(cfg).__name__}")


__all__ = ["Document", "Source", "load_source"]
