from __future__ import annotations
import json
from pathlib import Path
from typing import Iterator

from chunkshop.config import JsonCorpusSource as Cfg
from chunkshop.sources.base import Document


class JsonCorpusSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def iter_documents(self) -> Iterator[Document]:
        data = json.loads(Path(self.cfg.path).read_text())
        docs = data[self.cfg.documents_key]
        for row in docs:
            yield Document(
                id=row[self.cfg.id_field],
                content=row[self.cfg.content_field],
                title=row.get(self.cfg.title_field) if self.cfg.title_field else None,
                metadata={
                    k: v for k, v in row.items()
                    if k not in (self.cfg.id_field, self.cfg.content_field, self.cfg.title_field)
                },
            )
