"""Source that mines source files for comments and emits them as Documents.

Pairs with ``chunkshop.codeparse.comments.extract_comments`` to surface
inline rationale ("why does this function do X") that ``symbol_aware``
chunking otherwise buries inside function-body chunks. Yields one
``Document`` per comment block (default) or per file, depending on
``granularity``.

Why a Source and not a chunker / extractor? The user's mental model is
"comments are like docs — separate KB, sentence-aware chunked, queried
with prose vectors." A Source slot is the cleanest fit: it produces
``Document`` objects with a stable ``id`` and ``metadata``, and the
downstream chunker / embedder / sink are unchanged.

Lazy-imports: this module does NOT pull in tree-sitter. Python uses
stdlib tokenize+ast; everything else is regex. Importing
``chunkshop.sources.comment_extracts`` is cheap.
"""
from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import Iterator, Optional

from chunkshop.codeparse.comments import (
    CommentBlock,
    detect_language,
    extract_comments,
)
from chunkshop.config import CommentExtractsSource as Cfg
from chunkshop.sources.base import Document


class CommentExtractsSource:
    """Yields one Document per comment block (or per file) from globbed source files.

    Parameters
    ----------
    cfg :
        ``CommentExtractsSource`` pydantic config (glob, languages,
        min_chars, granularity, include_docstrings, skip_pragmas).

    Document shape
    --------------
    Per-block / per-line granularity::

        id       = "{path}::comment::{start_line}"
        content  = block.text
        title    = "{basename} comments at line {start_line}"
        metadata = {
            "source_path": "...",
            "start_line": 12,
            "end_line": 17,
            "language": "python",
            "kind": "line" | "block" | "docstring",
            "symbol": "Class.method" | None,   # python docstrings only
        }

    Per-file granularity::

        id       = "{path}::comments"
        content  = "\\n\\n".join(block.text for block in blocks)
        title    = "{basename} comments"
        metadata = {
            "source_path": "...",
            "language": "python",
            "block_count": 7,
            "first_line": 4,
            "last_line": 312,
        }
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        # Pre-normalise the optional language allowlist into a set for
        # quick membership tests. ``None`` means "auto-detect by ext, no
        # restriction".
        self._langs: Optional[set[str]] = (
            set(cfg.languages) if cfg.languages else None
        )

    # ------------------------------------------------------------------
    # Document emission
    # ------------------------------------------------------------------

    def iter_documents(self) -> Iterator[Document]:
        paths = sorted(_glob.glob(self.cfg.glob, recursive=True))
        if not paths:
            raise ValueError(f"no files matched glob: {self.cfg.glob}")

        for p in paths:
            path = Path(p)
            if not path.is_file():
                continue
            language = detect_language(path)
            if language is None:
                continue
            if self._langs is not None and language not in self._langs:
                continue

            blocks = extract_comments(
                path=path,
                language=language,
                skip_pragmas=self.cfg.skip_pragmas,
            )
            blocks = self._filter_blocks(blocks)
            if not blocks:
                continue

            if self.cfg.granularity == "per_file":
                yield self._emit_per_file(path, language, blocks)
            else:
                yield from self._emit_per_block(path, language, blocks)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _filter_blocks(self, blocks: list[CommentBlock]) -> list[CommentBlock]:
        """Apply ``include_docstrings`` and ``min_chars``."""
        out: list[CommentBlock] = []
        for b in blocks:
            if b.kind == "docstring" and not self.cfg.include_docstrings:
                continue
            if len(b.text) < self.cfg.min_chars:
                continue
            out.append(b)
        return out

    # ------------------------------------------------------------------
    # Emission strategies
    # ------------------------------------------------------------------

    def _emit_per_block(
        self,
        path: Path,
        language: str,
        blocks: list[CommentBlock],
    ) -> Iterator[Document]:
        if self.cfg.granularity == "per_line":
            # Explode multi-line ``CommentBlock`` into its lines. Each
            # line becomes its own Document. Block comments aren't
            # exploded — a ``/* multi-line */`` is conceptually one
            # comment even when ``per_line`` is set.
            exploded: list[CommentBlock] = []
            for b in blocks:
                if b.kind == "line" and "\n" in b.text:
                    line_texts = b.text.split("\n")
                    for i, lt in enumerate(line_texts):
                        ln = b.start_line + i
                        exploded.append(
                            CommentBlock(
                                text=lt.strip(),
                                start_line=ln,
                                end_line=ln,
                                kind="line",
                                language=b.language,
                                symbol=b.symbol,
                            )
                        )
                else:
                    exploded.append(b)
            # Re-apply min_chars on the exploded line texts so noise
            # lines from inside a block don't sneak past the filter.
            exploded = [b for b in exploded if len(b.text) >= self.cfg.min_chars]
            blocks = exploded

        for b in blocks:
            yield Document(
                id=f"{path}::comment::{b.start_line}",
                content=b.text,
                title=f"{path.name} comments at line {b.start_line}",
                metadata={
                    "source_path": str(path),
                    "start_line": b.start_line,
                    "end_line": b.end_line,
                    "language": language,
                    "kind": b.kind,
                    "symbol": b.symbol,
                },
            )

    def _emit_per_file(
        self,
        path: Path,
        language: str,
        blocks: list[CommentBlock],
    ) -> Document:
        content = "\n\n".join(b.text for b in blocks)
        return Document(
            id=f"{path}::comments",
            content=content,
            title=f"{path.name} comments",
            metadata={
                "source_path": str(path),
                "language": language,
                "block_count": len(blocks),
                "first_line": blocks[0].start_line,
                "last_line": blocks[-1].end_line,
            },
        )
