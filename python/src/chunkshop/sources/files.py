from __future__ import annotations

import glob as _glob
import hashlib
from pathlib import Path
from typing import Iterator, Optional

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.base import Document
from chunkshop.sources.parsers import DEFAULT_PARSERS, FileParser, get_parser
from chunkshop.sources.parsers.text import TextParser


class FilesSource:
    """Glob-driven file loader. Dispatches by extension to a `FileParser`.

    Parameters
    ----------
    cfg :
        FilesSource pydantic config (glob, encoding, id_from).
    parsers :
        Optional override map of extension -> FileParser. When omitted the
        module-level DEFAULT_PARSERS table is used, with the legacy
        ``cfg.encoding`` honored for text-family extensions via an injected
        TextParser.
    """

    # Extensions that should respect ``cfg.encoding`` for backward compat
    # when the caller didn't pass an explicit `parsers=` map.
    _TEXT_EXTS = ("txt", "md", "markdown", "rst", "log", "csv", "tsv", "")

    def __init__(
        self,
        cfg: Cfg,
        parsers: Optional[dict[str, FileParser]] = None,
    ):
        self.cfg = cfg
        if parsers is None:
            # Preserve ``cfg.encoding`` for text files. Build the effective
            # table by overlaying a TextParser(encoding=cfg.encoding) onto
            # the defaults for known text-family extensions.
            text_parser = TextParser(encoding=cfg.encoding)
            self._parsers: dict[str, FileParser] = dict(DEFAULT_PARSERS)
            for ext in self._TEXT_EXTS:
                self._parsers[ext] = text_parser
        else:
            self._parsers = dict(parsers)

    def iter_documents(self) -> Iterator[Document]:
        paths = sorted(_glob.glob(self.cfg.glob, recursive=True))
        if not paths:
            raise ValueError(f"no files matched glob: {self.cfg.glob}")
        for p in paths:
            path = Path(p)
            ext = path.suffix.lower().lstrip(".")
            parser = get_parser(ext, self._parsers)
            text = parser.parse(path)
            yield Document(
                id=self._id_for(path),
                content=text,
                title=path.name,
                metadata={
                    "source_path": str(path),
                    "parser": parser.__class__.__name__,
                },
            )

    def _id_for(self, path: Path) -> str:
        mode = self.cfg.id_from
        if mode == "path":
            return str(path)
        if mode == "stem":
            return path.stem
        if mode == "sha1":
            return hashlib.sha1(str(path).encode()).hexdigest()
        raise ValueError(mode)
