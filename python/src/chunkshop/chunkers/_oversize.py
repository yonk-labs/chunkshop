"""Shared helper for the if_oversize fallback chain.

Implements:
- The trigger condition: len(embedded_content) > ceiling OR len(original_content) > ceiling
- The dedup'd-once-per-cell warning (DedupedWarner)
- The recursion guard (max depth 5)
- apply_if_oversize() — called at the end of each chunker's chunk() method

Per Mission Brief SC-004, SC-006, SC-008. Decision D2: re-chunk original_content
of the offending chunk; each fallback chunk has embedded_content == original_content.
The wrapper's transformation (heading prefix, summary, neighbor join) is dropped
on the fallback path as the price of fitting the embedder.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable, Optional

from chunkshop.chunkers.base import Chunk
from chunkshop.sources.base import Document

log = logging.getLogger("chunkshop.chunkers.oversize")

MAX_RECURSION_DEPTH = 5


class OversizeRecursionError(RuntimeError):
    """Raised when an if_oversize chain exceeds MAX_RECURSION_DEPTH."""


class DedupedWarner:
    """Per-cell warning state. Emits exactly one WARN per chunker instance.

    Brief SC-006: log.warning(...) once per chunker instance (not per chunk).
    Names the chunker type, ceiling value, and a copy-paste suggestion.
    """

    def __init__(self, chunker_name: str, ceiling: int):
        self.chunker_name = chunker_name
        self.ceiling = ceiling
        self._warned = False

    def warn_once(self, oversize_len: int) -> None:
        if self._warned:
            return
        self._warned = True
        log.warning(
            "%s emitted oversize chunk(s) (>%d chars), no if_oversize fallback set; "
            "first oversize chunk has %d chars. "
            "To fix: add `if_oversize: { type: fixed_overlap, window_words: 200, "
            "step_words: 160, max_chars: %d }` to the chunker config "
            "(or pick another chunker as the fallback).",
            self.chunker_name,
            self.ceiling,
            oversize_len,
            self.ceiling,
        )


def _is_oversize(chunk: Chunk, ceiling: int) -> bool:
    """Brief SC-004 / D1: trigger on either field exceeding the ceiling."""
    return (
        len(chunk.embedded_content) > ceiling
        or len(chunk.original_content) > ceiling
    )


def apply_if_oversize(
    chunks: list[Chunk],
    *,
    ceiling: Optional[int],
    if_oversize_cfg,  # ChunkerConfig | None
    chunker_name: str,
    build_chunker: Callable,
    document: Document,
    depth: int = 0,
    skip_check: Optional[Callable[[Chunk], bool]] = None,
    warner: Optional[DedupedWarner] = None,
) -> list[Chunk]:
    """Apply the if_oversize fallback rule to a list of chunks.

    Returns the (possibly replaced) chunk list. If `if_oversize_cfg` is None and
    any chunk is oversize, emits ONE warning via `warner` and returns chunks
    unchanged. If set, oversize chunks are replaced by the output of running
    `build_chunker(if_oversize_cfg).chunk(...)` over a synthetic Document built
    from the offending chunk's original_content (D2).

    Args:
        chunks: emitted chunks from the parent chunker.
        ceiling: effective max_chars; if None, no enforcement happens.
        if_oversize_cfg: optional fallback chunker config.
        chunker_name: name of the parent chunker (for the warning text).
        build_chunker: a callable that takes a ChunkerConfig and returns a
            Chunker instance. Typically `chunkshop.chunkers.load_chunker`
            curried with the embedder context.
        document: the original document being chunked (passed through to
            fallback chunker for context).
        depth: current recursion depth (incremented per nested call).
        skip_check: optional predicate; chunks for which this returns True are
            exempt from the oversize check (used by hierarchical_summary for
            coarse rows — Brief SC-005).
        warner: DedupedWarner instance (per-chunker-instance state). If None,
            a fresh one is created (used in tests).

    Raises:
        OversizeRecursionError: if depth > MAX_RECURSION_DEPTH.
    """
    if depth > MAX_RECURSION_DEPTH:
        raise OversizeRecursionError(
            f"if_oversize chain exceeded depth {MAX_RECURSION_DEPTH} "
            f"(chunker={chunker_name!r}); revisit your config"
        )
    if ceiling is None:
        return chunks

    out: list[Chunk] = []
    seq = 0
    for c in chunks:
        if skip_check is not None and skip_check(c):
            out.append(replace(c, seq_num=seq))
            seq += 1
            continue

        if not _is_oversize(c, ceiling):
            out.append(replace(c, seq_num=seq))
            seq += 1
            continue

        # Oversize. Either fall back or warn.
        if if_oversize_cfg is None:
            if warner is not None:
                warner.warn_once(oversize_len=max(
                    len(c.embedded_content), len(c.original_content)
                ))
            out.append(replace(c, seq_num=seq))
            seq += 1
            continue

        # D2: re-chunk original_content; each fallback chunk has
        # embedded_content == original_content of the sub-chunk.
        synthetic_doc = Document(
            id=c.doc_id,
            content=c.original_content,
            metadata=document.metadata or {},
        )
        fallback_chunker = build_chunker(if_oversize_cfg)
        sub_chunks_raw = fallback_chunker.chunk(synthetic_doc)

        # Recursively apply if_oversize on the fallback's own output —
        # supports if_oversize chains.
        nested_cfg = getattr(if_oversize_cfg, "if_oversize", None)
        nested_ceiling = (
            if_oversize_cfg.effective_max_chars()
            if hasattr(if_oversize_cfg, "effective_max_chars")
            else None
        )
        sub_chunks = apply_if_oversize(
            sub_chunks_raw,
            ceiling=nested_ceiling,
            if_oversize_cfg=nested_cfg,
            chunker_name=getattr(if_oversize_cfg, "type", "fallback"),
            build_chunker=build_chunker,
            document=synthetic_doc,
            depth=depth + 1,
            skip_check=None,
            warner=warner,
        )

        # Propagate parent metadata where it doesn't conflict with fallback's own.
        for sc in sub_chunks:
            merged_meta = {**c.metadata, **sc.metadata}
            out.append(Chunk(
                doc_id=c.doc_id,
                seq_num=seq,
                original_content=sc.original_content,
                embedded_content=sc.original_content,  # D2: drop wrapper transform
                metadata=merged_meta,
            ))
            seq += 1
    return out
