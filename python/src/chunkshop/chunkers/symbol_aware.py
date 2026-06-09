"""Multi-language symbol-aware chunker (SP-B).

Splits source code at symbol boundaries (function / class) for any language
the :mod:`chunkshop.codeparse` package understands. Each emitted chunk's
``original_content`` is the raw source slice for that symbol; its
``embedded_content`` optionally prepends the file's import block as framing
context.

The chunker generalises the Python-only ``code_aware`` chunker (which uses
the stdlib ``ast`` module) to Python, Java, Go, TypeScript, and JavaScript
via SP-A's tree-sitter wrapper + regex fallback. Languages codeparse doesn't
recognise — or syntactically broken Python — fall back to a stock
``sentence_aware`` chunker with ``strategy='symbol_aware_fallback'`` so an
ingest pipeline never silently drops a document.

See ``docs/chunkers.md`` and the design plan §SP-B for the full contract.
"""
from __future__ import annotations

import ast
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._oversize import DedupedWarner, apply_if_oversize
from chunkshop.codeparse import (
    ParseResult,
    Symbol,
    build_fqn,
    build_scope_chain,
    code_symbol_node_id,
    parse_file,
)
from chunkshop.codeparse.langs import regex_fallback
from chunkshop.config import (
    SentenceAwareChunker as SentenceAwareCfg,
    SymbolAwareChunker as Cfg,
)
from chunkshop.sources.base import Document

log = logging.getLogger("chunkshop.chunkers.symbol_aware")


# Map a codeparse language tag back to a representative file extension so
# the temp-file we hand to ``parse_file`` is detected as the right language.
# Mirror of ``codeparse.langs.regex_fallback._EXT_TO_LANG`` (inverse).
_LANG_TO_EXT: dict[str, str] = {
    "python": ".py",
    "java": ".java",
    "go": ".go",
    "typescript": ".ts",
    "javascript": ".js",
    "rust": ".rs",
    "c": ".c",
    "cpp": ".cpp",
    "csharp": ".cs",
    "ruby": ".rb",
}


# Metadata keys, in priority order, that may carry a path we can extension-match
# to a language. Broadened beyond ``path``/``source_path`` because callers label
# the source file under many names (chunkshop#69).
_PATH_META_KEYS: tuple[str, ...] = (
    "path",
    "source_path",
    "file_path",
    "filepath",
    "filename",
    "rel_path",
    "relative_path",
    "uri",
    "url",
    "location",
    "name",
)


def _resolve_language(doc: Document, cfg: Cfg) -> Optional[str]:
    """Resolve the codeparse language for ``doc``, layered most- to least-explicit.

    Priority: (1) an explicit ``cfg.language`` override, (2) a
    ``doc.metadata['language']`` hint (tag or extension alias), (3) a path-like
    metadata value extension-matched to a language, (4) a path-shaped
    ``doc.id``, (5) a conservative content heuristic. Returns None only when the
    document carries no usable language signal at all — the caller then falls
    back to sentence_aware with ``fallback_reason='unsupported_language'``.
    """
    # 1. Operator-forced language for the whole cell (validated at config-load).
    if cfg.language:
        return cfg.language

    meta = doc.metadata or {}

    # 2. Per-document explicit hint. A junk value normalises to None and falls
    # through rather than forcing a wrong language.
    hint = meta.get("language")
    if isinstance(hint, str) and hint:
        lang = regex_fallback.normalize_language_tag(hint)
        if lang is not None:
            return lang

    # 3. Any path-like metadata value with a known extension.
    for key in _PATH_META_KEYS:
        val = meta.get(key)
        if isinstance(val, str) and val:
            lang = regex_fallback.detect_language(Path(val))
            if lang is not None:
                return lang

    # 4. A path-shaped doc id.
    doc_id = doc.id or ""
    if os.sep in doc_id or "/" in doc_id or "." in doc_id:
        lang = regex_fallback.detect_language(Path(doc_id))
        if lang is not None:
            return lang

    # 5. Last resort: guess from the source body.
    return regex_fallback.detect_language_from_content(doc.content or "")


def _slice_lines(content: str, start: int, end: int) -> str:
    """Return ``content`` lines [start, end] (1-based, inclusive).

    Both ``Symbol.line_start`` and ``Symbol.line_end`` come from tree-sitter /
    regex extractors that use 1-based line numbers (see codeparse/base.py).
    Out-of-range bounds are clamped — the parser can occasionally report
    end_line == start_line for one-line declarations.
    """
    if not content:
        return ""
    lines = content.splitlines()
    s = max(1, start) - 1
    e = max(s + 1, min(end, len(lines)))
    return "\n".join(lines[s:e])


def _parse_via_temp_file(
    content: str, language: str, *, project_id: str = "default"
) -> ParseResult:
    """Hand ``content`` to ``codeparse.parse_file`` via a temp file.

    ``parse_file`` takes a Path so it can stream bytes and detect language by
    extension. The chunker receives in-memory Document content, so we write
    a short-lived temp file with the matching extension. The file is unlinked
    in a ``finally`` block; we never leak temp files even on a parser crash.
    """
    suffix = _LANG_TO_EXT.get(language, ".txt")
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="chunkshop_symbol_aware_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        return parse_file(tmp, language=language, project_id=project_id)
    finally:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover — best-effort cleanup
            pass


def _python_has_syntax_error(content: str) -> bool:
    """Return True if ``ast.parse`` would raise on ``content``.

    tree-sitter is error-tolerant — it returns a (partial) tree for malformed
    Python — so we can't rely on the parser to flag a syntax error. The
    chunker explicitly checks Python sources via ``ast.parse`` so the
    documented "syntax error -> fallback" behaviour holds.
    """
    try:
        ast.parse(content)
    except SyntaxError:
        return True
    return False


class SymbolAwareChunker:
    """Splits source-code documents at symbol boundaries via codeparse."""

    def __init__(self, cfg: Cfg, build_chunker=None):
        self.cfg = cfg
        self._build_chunker = build_chunker
        self._warner = DedupedWarner("symbol_aware", cfg.max_chars)

    # --- public API --------------------------------------------------------

    def chunk(self, doc: Document) -> list[Chunk]:
        if not doc.content:
            return []

        # 1. Language detection: respect the explicit allowlist if set.
        language = _resolve_language(doc, self.cfg)
        if language is None:
            return self._fallback_chunks(doc, reason="unsupported_language")
        if self.cfg.languages is not None and language not in self.cfg.languages:
            return self._fallback_chunks(doc, reason="unsupported_language")

        # 2. Python-specific syntax-error guard. tree-sitter doesn't raise on
        # malformed Python but downstream consumers expect a syntactic guarantee
        # for ``strategy='symbol_aware'`` chunks, so we vet via ast.parse first.
        if language == "python" and _python_has_syntax_error(doc.content):
            return self._fallback_chunks(doc, reason="parse_error")

        # 3. Parse via codeparse (writes a temp file under the hood).
        try:
            result = _parse_via_temp_file(doc.content, language=language)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "symbol_aware: codeparse raised on doc %s (%s); falling back",
                doc.id,
                exc,
            )
            return self._fallback_chunks(doc, reason="parse_error")

        # An empty symbol list means "we recognised the language but found
        # nothing splittable" — treat as fallback so the document still
        # produces searchable chunks.
        if not result.symbols:
            return self._fallback_chunks(doc, reason="no_symbols")

        # 4. Re-derive FQNs against the document's *logical* path. parse_file
        # writes a temp file under the hood, so the FQNs codeparse returned
        # encode the random temp path — which would change every run and
        # break deterministic node_ids. We rebuild each symbol's FQN here
        # using the same ``build_fqn`` recipe but with the stable logical
        # path so the same file always produces the same node_id.
        logical_path = self._doc_file_path(doc)
        rebuilt_symbols = [
            Symbol(
                name=s.name,
                fqn=build_fqn(logical_path, s.name, s.parent_name),
                symbol_type=s.symbol_type,
                line_start=s.line_start,
                line_end=s.line_end,
                parent_name=s.parent_name,
            )
            for s in result.symbols
        ]
        result = ParseResult(
            symbols=rebuilt_symbols,
            call_sites=result.call_sites,
            imports=result.imports,
            language=result.language,
            parser=result.parser,
        )

        # 5. Granularity-specific emission.
        chunks = self._chunks_from_result(
            doc=doc, result=result, language=language
        )
        if not chunks:
            return self._fallback_chunks(doc, reason="no_symbols")

        # 5. Oversize handling — delegates to apply_if_oversize so the chain
        # matches the rest of the chunker zoo (same warning, same recursion
        # guard, same metadata propagation).
        return apply_if_oversize(
            chunks,
            ceiling=self.cfg.effective_max_chars(),
            if_oversize_cfg=self.cfg.if_oversize,
            chunker_name="symbol_aware",
            build_chunker=self._build_chunker,
            document=doc,
            warner=self._warner,
        )

    # --- chunk construction -----------------------------------------------

    def _chunks_from_result(
        self, *, doc: Document, result: ParseResult, language: str
    ) -> list[Chunk]:
        granularity = self.cfg.granularity
        imports_block = (
            "\n".join(result.imports) if self.cfg.include_imports else ""
        )

        if granularity == "module":
            return [self._build_module_chunk(doc, language, imports_block)]

        # Top-level symbols only — methods inside a class bundle into the class
        # chunk via the class's full line span (parent_name is set on methods).
        top_level = [s for s in result.symbols if s.parent_name is None]
        if not top_level:
            return []

        out: list[Chunk] = []
        if granularity == "class":
            classes = [s for s in top_level if s.symbol_type == "class"]
            funcs = [s for s in top_level if s.symbol_type == "function"]
            for sym in classes:
                out.append(
                    self._build_symbol_chunk(
                        doc, sym, language, imports_block, seq=len(out)
                    )
                )
            if funcs:
                out.append(
                    self._build_module_block_chunk(
                        doc, funcs, language, imports_block, seq=len(out)
                    )
                )
            return out

        # granularity == "function"
        for sym in top_level:
            out.append(
                self._build_symbol_chunk(
                    doc, sym, language, imports_block, seq=len(out)
                )
            )
        return out

    def _build_symbol_chunk(
        self,
        doc: Document,
        sym: Symbol,
        language: str,
        imports_block: str,
        *,
        seq: int,
    ) -> Chunk:
        source = _slice_lines(doc.content, sym.line_start, sym.line_end)
        embedded = self._frame(source, sym.name, imports_block)
        file_path = self._doc_file_path(doc)
        node_id = code_symbol_node_id(
            "default", language, file_path, sym.fqn
        )
        return Chunk(
            doc_id=doc.id,
            seq_num=seq,
            original_content=source,
            embedded_content=embedded,
            metadata={
                "strategy": "symbol_aware",
                "symbol_name": sym.name,
                "fqn": sym.fqn,
                "scope_chain": build_scope_chain(
                    file_path, sym.name, sym.parent_name
                ),
                "symbol_type": sym.symbol_type,
                "start_line": sym.line_start,
                "end_line": sym.line_end,
                "parent_name": sym.parent_name,
                "language": language,
                "node_id": node_id,
            },
        )

    def _build_module_block_chunk(
        self,
        doc: Document,
        free_funcs: list[Symbol],
        language: str,
        imports_block: str,
        *,
        seq: int,
    ) -> Chunk:
        """Emit a single ``module_block`` chunk concatenating free top-level fns.

        Used by ``granularity='class'`` to keep free-floating helpers together
        rather than scattering one chunk per tiny helper.
        """
        sorted_funcs = sorted(free_funcs, key=lambda s: s.line_start)
        parts = [
            _slice_lines(doc.content, s.line_start, s.line_end)
            for s in sorted_funcs
        ]
        parts = [p for p in parts if p]
        source = "\n\n".join(parts)
        start_line = sorted_funcs[0].line_start
        end_line = sorted_funcs[-1].line_end
        # The module_block doesn't map to a single symbol; build a synthetic
        # FQN from the file stem + "<module_block>" so node_id stays
        # deterministic across runs.
        file_path = self._doc_file_path(doc)
        synthetic_fqn = build_fqn(file_path, "<module_block>", None)
        node_id = code_symbol_node_id(
            "default", language, file_path, synthetic_fqn
        )
        embedded = self._frame(source, "<module_block>", imports_block)
        return Chunk(
            doc_id=doc.id,
            seq_num=seq,
            original_content=source,
            embedded_content=embedded,
            metadata={
                "strategy": "symbol_aware",
                "symbol_name": "<module_block>",
                "fqn": synthetic_fqn,
                "scope_chain": build_scope_chain(
                    file_path, "<module_block>", None
                ),
                "symbol_type": "module_block",
                "start_line": start_line,
                "end_line": end_line,
                "parent_name": None,
                "language": language,
                "node_id": node_id,
            },
        )

    def _build_module_chunk(
        self, doc: Document, language: str, imports_block: str
    ) -> Chunk:
        """``granularity='module'`` emits the whole file as a single chunk."""
        source = doc.content
        file_path = self._doc_file_path(doc)
        synthetic_fqn = build_fqn(file_path, "<module>", None)
        node_id = code_symbol_node_id(
            "default", language, file_path, synthetic_fqn
        )
        lines = source.splitlines()
        embedded = self._frame(source, "<module>", imports_block)
        return Chunk(
            doc_id=doc.id,
            seq_num=0,
            original_content=source,
            embedded_content=embedded,
            metadata={
                "strategy": "symbol_aware",
                "symbol_name": "<module>",
                "fqn": synthetic_fqn,
                "scope_chain": build_scope_chain(file_path, "<module>", None),
                "symbol_type": "module",
                "start_line": 1,
                "end_line": max(len(lines), 1),
                "parent_name": None,
                "language": language,
                "node_id": node_id,
            },
        )

    def _frame(self, source: str, node_name: str, imports_block: str) -> str:
        """Prepend the file's import block to ``source`` for embedding context.

        Mirrors ``code_aware._frame_for_embedding``: ``embedded_content`` carries
        the framing, ``original_content`` stays raw. With ``include_imports=False``
        the two are identical.
        """
        if not imports_block:
            return source
        return f"{imports_block}\n\n# Definition: {node_name}\n{source}"

    def _doc_file_path(self, doc: Document) -> str:
        """Best path-like identifier for FQN / node_id derivation.

        Order: ``metadata.path`` > ``metadata.source_path`` > ``doc.id`` if it
        looks path-shaped, else doc.id verbatim. Mirrors what the other
        chunkers + sinks use so a node_id stamped here matches the node_id
        a downstream graph-store would mint for the same symbol.
        """
        meta = doc.metadata or {}
        for key in ("path", "source_path"):
            val = meta.get(key)
            if isinstance(val, str) and val:
                return val
        return doc.id or "unknown"

    # --- fallback ----------------------------------------------------------

    def _fallback_chunks(self, doc: Document, *, reason: str) -> list[Chunk]:
        """Run SentenceAwareChunker and tag every emitted chunk for forensics.

        Tagging carries ``strategy='symbol_aware_fallback'`` plus the specific
        ``fallback_reason`` so a downstream search query can filter to "ingest
        documents that didn't take the symbol-aware path" without re-parsing
        the source.
        """
        default_cfg = SentenceAwareCfg(
            type="sentence_aware",
            max_chars=min(self.cfg.max_chars, 2000),
        )
        if self._build_chunker is not None:
            fallback = self._build_chunker(default_cfg)
        else:  # pragma: no cover — only hit by tests that bypass the factory
            from chunkshop.chunkers.sentence_aware import (
                SentenceAwareChunker as SentenceAwareImpl,
            )
            fallback = SentenceAwareImpl(default_cfg)

        out: list[Chunk] = []
        for c in fallback.chunk(doc):
            merged = {
                **c.metadata,
                "strategy": "symbol_aware_fallback",
                "fallback_reason": reason,
            }
            out.append(
                Chunk(
                    doc_id=c.doc_id,
                    seq_num=c.seq_num,
                    original_content=c.original_content,
                    embedded_content=c.embedded_content,
                    metadata=merged,
                )
            )
        return out


__all__ = ["SymbolAwareChunker"]
