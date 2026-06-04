from __future__ import annotations

import glob as _glob
import hashlib
from pathlib import Path
from typing import Iterator, Optional

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.base import Document, SyncMode
from chunkshop.sources.parsers import DEFAULT_PARSERS, FileParser, get_parser
from chunkshop.sources.parsers.text import TextParser


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FilesSource:
    """Glob-driven file loader. Dispatches by extension to a `FileParser`.

    Implements ``IncrementalSource`` and ``PrunableSource``: the cursor is a
    ``{path: {"h": sha256, "mt": mtime, "sz": size}}`` map. ``hash`` detection
    (default) compares the content hash; ``mtime`` detection trusts
    ``(mtime, size)`` and never reads unchanged files. ``sync_mode`` is always
    ``CURSOR`` — the source *can* sync incrementally; whether the CLI does is
    gated by ``cfg.incremental`` in the runner.
    """

    # Extensions that should respect ``cfg.encoding`` for backward compat
    # when the caller didn't pass an explicit `parsers=` map.
    _TEXT_EXTS = ("txt", "md", "markdown", "rst", "log", "csv", "tsv", "")

    sync_mode = SyncMode.CURSOR

    def __init__(
        self,
        cfg: Cfg,
        parsers: Optional[dict[str, FileParser]] = None,
    ):
        self.cfg = cfg
        self._detect = cfg.incremental.detect if cfg.incremental else "hash"
        if parsers is None:
            text_parser = TextParser(encoding=cfg.encoding)
            self._parsers: dict[str, FileParser] = dict(DEFAULT_PARSERS)
            for ext in self._TEXT_EXTS:
                self._parsers[ext] = text_parser
        else:
            self._parsers = dict(parsers)

    # --- full-resync path (unchanged behavior) ---------------------------

    def iter_documents(self) -> Iterator[Document]:
        paths = self.current_paths()
        if not paths:
            raise ValueError(f"no files matched glob: {self.cfg.glob}")
        for p in paths:
            yield self._document_for(Path(p))

    # --- IncrementalSource ------------------------------------------------

    def current_paths(self) -> list[str]:
        """Sorted list of paths currently matching the glob — no file reads.

        Normalized through ``Path`` so the strings here match the ``source_path``
        stamped on each ``Document`` (and therefore the cursor keys), regardless
        of glob form. Without this, a relative glob like ``./corpus/**/*.md``
        yields ``./corpus/a.md`` here but ``corpus/a.md`` on the Document — the
        cursor trim would then drop every entry and the source would silently
        full-resync on every run.
        """
        return sorted(str(Path(p)) for p in _glob.glob(self.cfg.glob, recursive=True))

    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterator[Document]:
        # NOTE: unlike iter_documents, an empty match set is NOT an error here —
        # on a non-first run it legitimately means every file was deleted; the
        # runner's prune step handles that.
        for p in self.current_paths():
            path = Path(p)
            prev = cursor.get(p)
            st = path.stat()
            if (
                self._detect == "mtime"
                and prev is not None
                and prev.get("mt") == st.st_mtime
                and prev.get("sz") == st.st_size
            ):
                continue  # unchanged by stat — skip without reading bytes
            content_hash = _hash_bytes(path.read_bytes())
            if prev is not None and prev.get("h") == content_hash:
                continue  # unchanged content
            yield self._document_for(path, content_hash)

    def cursor_from(self, last_document: Document) -> dict:
        meta = last_document.metadata or {}
        p = meta.get("source_path", last_document.id)
        st = Path(p).stat()
        return {p: {"h": last_document.fingerprint, "mt": st.st_mtime, "sz": st.st_size}}

    # --- PrunableSource ---------------------------------------------------

    def empty_prune_cursor(self) -> dict:
        return {}

    def iter_deleted_since(self, cursor: dict) -> Iterator[str]:
        """Yield doc_ids for files present in ``cursor`` but absent on disk.

        The cursor is keyed by path; deletion IDs are the ``Document.id`` values
        (via ``_id_for``), so the consumer can pass them straight to
        ``sink.delete_document``.
        """
        current = set(self.current_paths())
        for p in cursor:
            if p not in current:
                yield self._id_for(Path(p))

    # --- shared helpers ---------------------------------------------------

    def _document_for(self, path: Path, content_hash: Optional[str] = None) -> Document:
        ext = path.suffix.lower().lstrip(".")
        parser = get_parser(ext, self._parsers)
        text = parser.parse(path)
        return Document(
            id=self._id_for(path),
            content=text,
            title=path.name,
            metadata={
                "source_path": str(path),
                "parser": parser.__class__.__name__,
            },
            fingerprint=content_hash,
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
