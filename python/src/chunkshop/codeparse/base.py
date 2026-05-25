"""Pydantic v2 models for code-symbol extraction.

These three types are the public surface that downstream SP-B / SP-C / SP-D
consume. They intentionally lift the dataclass shape from the migration server
(``yonk-full-stack-mig-srv/v2/backend/app/engine/indexer``) but as pydantic v2
models so chunkshop's existing ``extra='forbid'`` + validation discipline
applies. The schema is frozen for v1 — adding fields needs an SP-A change,
not a per-language tweak.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Chunkshop's house policy: reject unknown keys at parse time.

    Mirrors the discipline used by ``chunkshop.config._Base``. A typo in a
    Symbol dict is a bug — silently swallowing it would mask language-bundle
    output drift.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)


class Symbol(_Base):
    """A code symbol extracted from a single source file.

    ``fqn`` is the deterministic fully-qualified name produced by
    :func:`chunkshop.codeparse.fqn.build_fqn`. ``line_start`` / ``line_end``
    are 1-based and inclusive, matching tree-sitter's ``start_point[0] + 1``
    semantics so they're greppable against the source. ``parent_name`` is
    set for methods nested inside a class; ``None`` for top-level functions
    and classes.
    """

    name: str
    fqn: str
    symbol_type: str  # "function" | "class" | "method" | "interface" | "module"
    line_start: int
    line_end: int
    parent_name: Optional[str] = None


class CallSite(_Base):
    """A single observed call inside a source file.

    Mirrors yonk-full-stack-mig-srv's ``CallSite`` dataclass so the SP-C
    cross-file resolver (and its conservative name-unique posture) can be
    lifted with minimal rewrite. ``caller_node_id`` is the enclosing
    function/method's :func:`code_symbol_node_id` so a downstream resolver
    can JOIN call sites to symbols without an extra lookup.
    ``resolved_intra_file=True`` means the per-language extractor already
    matched the callee to a sibling symbol in the same file — the
    cross-file resolver should skip these to avoid double-counting edges.
    """

    caller_node_id: str
    callee_name: str
    line: int
    snippet: str
    parser: str
    resolved_intra_file: bool = False


class ParseResult(_Base):
    """Result of parsing a single file.

    ``raw_tree`` deliberately omitted from the v1 pydantic model: tree-sitter
    Tree objects aren't pydantic-serializable, and downstream chunkers /
    extractors only need the structured ``symbols`` + ``call_sites`` +
    ``imports`` lists. If a future feature needs the raw tree it should
    re-parse, not pickle.
    """

    symbols: list[Symbol] = Field(default_factory=list)
    call_sites: list[CallSite] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    language: Optional[str] = None
    parser: Optional[str] = None  # "tree-sitter" | "regex"


__all__ = ["Symbol", "CallSite", "ParseResult"]
