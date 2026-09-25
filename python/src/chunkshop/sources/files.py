from __future__ import annotations

import glob as _glob
import hashlib
from pathlib import Path
from typing import Iterator, Optional

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.base import Document, SyncMode
from chunkshop.sources.derivers.cache import cached_derive
from chunkshop.sources.parsers import (
    DEFAULT_PARSERS,
    EmptyDerivationError,
    FileParser,
    cache_key_extra_for,
    call_deriver,
    get_parser,
)
from chunkshop.sources.parsers.text import TextParser


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: Where to send a user whose corpus genuinely is scanned images. chunkshop
#: does not OCR and does not intend to; the honest remedy is preprocessing.
_PREPROCESS_HINT = (
    "preprocess it to markdown first (e.g. yonk-doctools, which does "
    "PDF/DOCX -> markdown with image captioning) and ingest that output"
)


def _empty_message(path: Path, parser, had_content: str | None) -> str:
    """The text of an EmptyDerivationError.

    Two distinct failures share one exception type, and they need different
    advice. A deriver that produced nothing is a rung/config problem. A
    PARSER that read real content units and produced nothing is F5's case —
    a scanned page, an image-only deck — and the user needs to be told what
    the file actually contained and that preprocessing, not a config flag,
    is the real fix.
    """
    name = parser.__class__.__name__
    if had_content:
        return (
            f"{path}: {name} read {had_content} but extracted no text. "
            f"The file has content that produced no characters — typically a "
            f"scanned PDF or an image-only page with no text layer. chunkshop "
            f"does not OCR: {_PREPROCESS_HINT}. To continue past files like "
            f"this instead, set source.on_empty='skip' (or 'index' to index "
            f"it as an empty document anyway)."
        )
    return (
        f"{path} produced no text via {name}. "
        f"Set source.on_empty='skip' to continue past these files, "
        f"or 'index' to restore pre-1.0 behaviour."
        " If this file needs OCR (e.g. a scanned PDF or "
        "image), configure an OCR-capable deriver for it."
    )


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

        # Derive config layers over the parser table: one deriver per
        # extension listed in a pipe-separated key ("png|jpg|webp"). Track
        # exactly which extensions came from this block — the cache below is
        # scoped to only those, never to the default/injected parser table.
        #
        # Task 9 Correction 2: reject a `derive:` block where the same
        # extension is reachable from two DIFFERENT keys, rather than
        # silently letting the later key win (see AmbiguousDeriverError's
        # docstring — Rust's BTreeMap-ordered resolution can pick the
        # opposite winner for the identical config).
        self._derived_exts: set[str] = set()
        if cfg.derive:
            from chunkshop.sources.derivers import load_deriver
            from chunkshop.sources.parsers import AmbiguousDeriverError

            ext_owner: dict[str, str] = {}
            for pattern, deriver_cfg in cfg.derive.items():
                deriver = load_deriver(deriver_cfg)
                for ext in pattern.split("|"):
                    ext = ext.strip().lower().lstrip(".")
                    owner = ext_owner.get(ext)
                    if owner is not None and owner != pattern:
                        raise AmbiguousDeriverError(
                            f"extension '{ext}' is reachable from two different "
                            f"`derive` keys: '{owner}' and '{pattern}'. Each "
                            f"extension must be routed by exactly one `derive` key."
                        )
                    ext_owner[ext] = pattern
                    self._parsers[ext] = deriver
                    self._derived_exts.add(ext)

        # The cache exists to memoise EXPENSIVE derivation. Without a derive
        # block every parser is cheap local text extraction, so a text-only
        # cell builds no cache and touches no disk (NN-6). Even with a
        # derive block, only derive-configured extensions are ever routed
        # through it — see _document_for — so a config-dependent parser
        # (e.g. TextParser's `encoding`) can never be served a stale,
        # differently-configured cache hit.
        self._cache = None
        if cfg.derive and cfg.cache.enabled:
            from chunkshop.sources.derivers.cache import DerivationCache

            self._cache = DerivationCache(
                root=Path(cfg.cache.root) if cfg.cache.root else None
            )

    # --- full-resync path (unchanged behavior) ---------------------------

    def iter_documents(self) -> Iterator[Document]:
        paths = self.current_paths()
        if not paths:
            raise ValueError(f"no files matched glob: {self.cfg.glob}")
        for p in paths:
            doc = self._document_for(Path(p))
            if doc is not None:
                yield doc

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
            doc = self._document_for(path, content_hash)
            if doc is not None:
                yield doc

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

    def _document_for(self, path: Path, content_hash: Optional[str] = None) -> Optional[Document]:
        ext = path.suffix.lower().lstrip(".")
        parser = get_parser(ext, self._parsers)
        # ONE routing signal, read once and reused below. "this extension is
        # claimed by the `derive:` block" and "this document actually went
        # through a Deriver" are the same fact here, not two: the derive
        # block OVERWRITES ``self._parsers[ext]`` (see __init__), so
        # ``get_parser`` cannot hand back a text parser for a derive-routed
        # extension, and a deriver that cannot be built raises out of
        # ``load_deriver`` at construction rather than degrading to one.
        # Keeping it a single boolean is deliberate — a second, parallel
        # notion of "was this derived" is exactly the defect class that
        # produced the bug this gate fixes.
        derived = ext in self._derived_exts
        if derived:
            # Only derive-configured extensions are cache-eligible. Routing
            # the default/injected parser table through the cache too would
            # key it on parser.__class__.__name__ alone — collapsing e.g.
            # every TextParser regardless of its `encoding` onto one cache
            # entry and silently serving a stale/wrongly-decoded hit.
            if content_hash is None:
                content_hash = _hash_bytes(path.read_bytes())
            d_hash = getattr(parser, "derivation_hash", None)
            if callable(d_hash):
                d_hash = d_hash()
            if d_hash is None:
                d_hash = parser.__class__.__name__
            # Third component of the key: whatever per-document material
            # this deriver consumes beyond its bytes (None for a pure
            # deriver — see `parsers.base.Deriver`'s cache-soundness
            # invariant). Without it, `ImageMetaDeriver` — whose text comes
            # from the FILENAME — served byte-identical images each other's
            # derived text.
            extra = cache_key_extra_for(parser, path)
            result, _was_hit = cached_derive(
                self._cache,
                content_hash,
                d_hash,
                lambda: call_deriver(parser, path),
                extra,
            )
        else:
            result = call_deriver(parser, path)
        if not result.text.strip():
            # ``on_empty`` reaches a document by exactly TWO routes, and
            # "the text came out empty" is not one of them.
            #
            # (1) The document was derive-routed — read ``on_empty``'s
            #     docstring on ``FilesSource``: "What to do when a deriver
            #     produces no text."
            # (2) The parser made an affirmative ``had_content`` claim: it
            #     counted content units it knows how to read (PDF pages,
            #     DOCX tables, PPTX slides, embedded HTML elements) and
            #     produced no characters anyway. That is spec defect F5 —
            #     "empty-derivation is loud, not silent" — and a scanned PDF
            #     is its canonical case.
            #
            # Everything else is indexed unconditionally, which is what
            # keeps a legitimately-empty text file legitimately empty.
            # Applying a derivation policy to the plain-text path was a
            # data-loss regression for text-only corpora: a zero-byte or
            # whitespace-only .md has always been indexed as an empty
            # document, and the default ``error`` turned an upgrade into an
            # aborted run — after the sink's ``mode: overwrite`` DDL had
            # already dropped the populated table, and with no way to
            # pre-stage ``on_empty: index`` because the older pydantic model
            # rejects the unknown key. Route (2) cannot resurrect that bug:
            # ``TextParser`` has no notion of a content unit and never sets
            # ``had_content``, so no text file can reach the loud branch no
            # matter how empty it is. Widening the trigger back to "text is
            # blank" is exactly the change this comment exists to prevent.
            mode = (
                self.cfg.on_empty
                if (derived or result.had_content)
                else "index"
            )
            if mode == "error":
                raise EmptyDerivationError(
                    _empty_message(path, parser, result.had_content)
                )
            if mode == "skip":
                return None
        metadata = {
            **result.metadata,
            # chunkshop's own provenance always wins over a deriver's keys.
            "source_path": str(path),
            "parser": parser.__class__.__name__,
        }
        return Document(
            id=self._id_for(path),
            content=result.text,
            title=path.name,
            metadata=metadata,
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
