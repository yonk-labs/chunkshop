"""Code-aware AST chunker for Python source files.

Splits at top-level function/class boundaries via the stdlib ``ast`` module
(zero runtime deps). Module-level statements gather into a leading
``module_block`` chunk so imports + constants don't get sliced mid-statement.
Each emitted chunk's ``original_content`` is the raw source segment from
``ast.get_source_segment``; ``embedded_content`` optionally prefixes the file's
import block for context.

Non-Python documents fall back to a sub-chunker (the configured ``if_oversize``
chunker if set, otherwise a default ``sentence_aware``). Malformed Python is
logged and emitted as a single chunk with ``strategy='code_aware_fallback'``.

See docs/chunkers.md for tuning guidance.
"""
from __future__ import annotations

import ast
import logging
import os
from typing import Optional

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._oversize import DedupedWarner, apply_if_oversize
from chunkshop.config import CodeAwareChunker as Cfg
from chunkshop.config import SentenceAwareChunker as SentenceAwareCfg
from chunkshop.sources.base import Document

log = logging.getLogger("chunkshop.chunkers.code_aware")

# AST node types we treat as "top-level definition" boundaries.
_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_IMPORT_NODES = (ast.Import, ast.ImportFrom)


def _is_python_path(path: str) -> bool:
    if not path:
        return False
    return path.lower().endswith(".py")


def _node_kind(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function"
    return "module_block"


def _node_name(node: ast.AST) -> str:
    # Defs carry a `name`; module-level statements don't.
    return getattr(node, "name", "") or "<module>"


def _segment(source: str, node: ast.AST) -> str:
    """Return the raw source for ``node`` from the original text.

    ``ast.get_source_segment`` handles multi-line nodes and preserves indentation.
    For nodes without line info it returns None — we fall back to an empty string.
    """
    seg = ast.get_source_segment(source, node)
    return seg or ""


def _is_docstring_node(node: ast.AST) -> bool:
    """Module/function docstring node (an Expr wrapping a string constant)."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(getattr(node, "value", None), ast.Constant)
        and isinstance(node.value.value, str)
    )


def _extract_imports_block(tree: ast.AST, source: str) -> str:
    """Return the contiguous import block near the top of the module.

    Walks top-level statements collecting any Import / ImportFrom nodes that
    appear before the first definition (FunctionDef / AsyncFunctionDef /
    ClassDef). Skips a leading docstring (very common) and tolerates module
    constants interleaved between imports — both patterns are extremely common
    in real-world Python and shouldn't suppress the import-framing prefix.
    """
    import_lines: list[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, _DEF_NODES):
            break
        if isinstance(node, _IMPORT_NODES):
            seg = _segment(source, node)
            if seg:
                import_lines.append(seg)
        # Non-import, non-def, non-docstring statements (module constants,
        # __all__, etc.) are silently skipped — they're emitted as part of the
        # module_block chunk, not the import-context prefix.
        elif _is_docstring_node(node):
            continue
        else:
            continue
    return "\n".join(import_lines)


class CodeAwareChunker:
    """Split Python source at function/class boundaries; fall back otherwise."""

    def __init__(self, cfg: Cfg, build_chunker=None):
        self.cfg = cfg
        self._build_chunker = build_chunker
        self._warner = DedupedWarner("code_aware", cfg.max_chars)

    # --- public API --------------------------------------------------------

    def chunk(self, doc: Document) -> list[Chunk]:
        if not doc.content:
            return []

        if not self._is_python_doc(doc):
            return self._fallback_chunks(doc)

        try:
            tree = ast.parse(doc.content)
        except SyntaxError as exc:
            log.warning(
                "code_aware: failed to parse doc id=%s as Python (%s); "
                "emitting single fallback chunk",
                doc.id,
                exc,
            )
            return [
                Chunk(
                    doc_id=doc.id,
                    seq_num=0,
                    original_content=doc.content,
                    embedded_content=doc.content,
                    metadata={
                        "strategy": "code_aware_fallback",
                        "node_type": "fallback",
                        "node_name": "<unparsable>",
                    },
                )
            ]

        chunks = self._chunks_from_tree(tree, doc)

        return apply_if_oversize(
            chunks,
            ceiling=self.cfg.effective_max_chars(),
            if_oversize_cfg=self.cfg.if_oversize,
            chunker_name="code_aware",
            build_chunker=self._build_chunker,
            document=doc,
            warner=self._warner,
        )

    # --- internals ---------------------------------------------------------

    def _is_python_doc(self, doc: Document) -> bool:
        if self.cfg.language == "python":
            return True
        # language == "auto": sniff by extension. Look at common metadata keys
        # the various sources use (FilesSource and the rich-document parsers
        # store the path under `path`; some store `source_path`).
        meta = doc.metadata or {}
        for key in ("path", "source_path"):
            val = meta.get(key)
            if isinstance(val, str) and _is_python_path(val):
                return True
        # Fall back to the document id when it looks like a path. Some sources
        # (`FilesSource(id_from='path')`) put the full file path in `doc.id`.
        doc_id = doc.id or ""
        if os.sep in doc_id or "/" in doc_id:
            if _is_python_path(doc_id):
                return True
        return False

    def _chunks_from_tree(self, tree: ast.Module, doc: Document) -> list[Chunk]:
        """Walk top-level nodes and emit one chunk per def/class + a module_block."""
        imports_block = (
            _extract_imports_block(tree, doc.content)
            if self.cfg.include_imports
            else ""
        )

        body = list(getattr(tree, "body", []))
        if not body:
            return []

        out: list[Chunk] = []
        seq = 0
        pending_module_nodes: list[ast.AST] = []

        for node in body:
            if isinstance(node, _DEF_NODES):
                # Flush any pending module-level block first so it precedes
                # this definition in the output stream.
                if pending_module_nodes:
                    mb_chunk = self._build_module_block_chunk(
                        pending_module_nodes, doc, seq, imports_block
                    )
                    if mb_chunk is not None:
                        out.append(mb_chunk)
                        seq += 1
                    pending_module_nodes = []
                def_chunk = self._build_def_chunk(node, doc, seq, imports_block)
                if def_chunk is not None:
                    out.append(def_chunk)
                    seq += 1
            else:
                pending_module_nodes.append(node)

        # Trailing module-level statements after the last def — still emit them.
        if pending_module_nodes:
            mb_chunk = self._build_module_block_chunk(
                pending_module_nodes, doc, seq, imports_block
            )
            if mb_chunk is not None:
                out.append(mb_chunk)
                seq += 1

        return out

    def _build_def_chunk(
        self,
        node: ast.AST,
        doc: Document,
        seq: int,
        imports_block: str,
    ) -> Optional[Chunk]:
        source = _segment(doc.content, node)
        if not source:
            return None
        node_name = _node_name(node)
        node_type = _node_kind(node)
        embedded = self._frame_for_embedding(source, node_name, imports_block)
        return Chunk(
            doc_id=doc.id,
            seq_num=seq,
            original_content=source,
            embedded_content=embedded,
            metadata={
                "strategy": "code_aware",
                "node_type": node_type,
                "node_name": node_name,
                "start_line": getattr(node, "lineno", 1),
                "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
            },
        )

    def _build_module_block_chunk(
        self,
        nodes: list[ast.AST],
        doc: Document,
        seq: int,
        imports_block: str,
    ) -> Optional[Chunk]:
        if not nodes:
            return None
        # Join the source segments for each statement. Using `\n\n` as a join
        # preserves a reasonable visual gap between unrelated statements.
        parts = [_segment(doc.content, n) for n in nodes]
        parts = [p for p in parts if p]
        if not parts:
            return None
        source = "\n".join(parts)
        # Skip empty / whitespace-only blocks.
        if not source.strip():
            return None
        start_line = getattr(nodes[0], "lineno", 1)
        end_line = getattr(
            nodes[-1], "end_lineno", getattr(nodes[-1], "lineno", start_line)
        )
        embedded = self._frame_for_embedding(source, "<module>", imports_block)
        return Chunk(
            doc_id=doc.id,
            seq_num=seq,
            original_content=source,
            embedded_content=embedded,
            metadata={
                "strategy": "code_aware",
                "node_type": "module_block",
                "node_name": "<module>",
                "start_line": start_line,
                "end_line": end_line,
            },
        )

    def _frame_for_embedding(
        self, source: str, node_name: str, imports_block: str
    ) -> str:
        if not imports_block:
            return source
        return f"{imports_block}\n\n# Definition: {node_name}\n{source}"

    # --- non-Python fallback path -----------------------------------------

    def _fallback_chunks(self, doc: Document) -> list[Chunk]:
        """Delegate to the configured if_oversize chunker, or a default sentence_aware.

        This is the "auto" branch for non-Python documents. We deliberately do
        NOT pass through ``apply_if_oversize`` here — the fallback chunker owns
        its own oversize handling (its own ``if_oversize`` if configured).
        """
        if self.cfg.if_oversize is not None and self._build_chunker is not None:
            fallback = self._build_chunker(self.cfg.if_oversize)
            return list(fallback.chunk(doc))
        # Default: sentence_aware with reasonable defaults aligned to our max_chars.
        default_cfg = SentenceAwareCfg(
            type="sentence_aware",
            max_chars=min(self.cfg.max_chars, 2000),
            min_chars=min(self.cfg.min_chars, 200),
        )
        if self._build_chunker is not None:
            fallback = self._build_chunker(default_cfg)
        else:
            # Bare instantiation for the no-builder case (e.g. unit tests that
            # call CodeAwareChunker(cfg) directly without going through
            # load_chunker). Local import avoids a circular dep at module load.
            from chunkshop.chunkers.sentence_aware import (
                SentenceAwareChunker as SentenceAwareImpl,
            )
            fallback = SentenceAwareImpl(default_cfg)
        return list(fallback.chunk(doc))
