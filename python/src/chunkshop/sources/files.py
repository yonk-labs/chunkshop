from __future__ import annotations
import glob as _glob
import hashlib
from pathlib import Path
from typing import Iterator

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.base import Document


class FilesSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def iter_documents(self) -> Iterator[Document]:
        paths = sorted(_glob.glob(self.cfg.glob, recursive=True))
        if not paths:
            raise ValueError(f"no files matched glob: {self.cfg.glob}")
        for p in paths:
            path = Path(p)
            text = path.read_text(encoding=self.cfg.encoding, errors="replace")
            doc_id = self._id_for(path)
            yield Document(id=doc_id, content=text, title=path.name, metadata={"source_path": str(path)})

    def _id_for(self, path: Path) -> str:
        mode = self.cfg.id_from
        if mode == "path":
            return str(path)
        if mode == "stem":
            return path.stem
        if mode == "sha1":
            return hashlib.sha1(str(path).encode()).hexdigest()
        raise ValueError(mode)
