"""ClickHouse table source — see docs/superpowers/specs/2026-05-05-p1-py-clickhouse-source-design.md."""
from __future__ import annotations
from typing import Iterator

from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.config import ClickhouseTableSource as Cfg
from chunkshop.sources.base import Document


class ClickhouseTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = ClickHouseBackend(dsn_env=cfg.dsn_env)

    def iter_documents(self) -> Iterator[Document]:
        raise NotImplementedError("filled in by Task 3")
