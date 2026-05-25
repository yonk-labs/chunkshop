"""Deterministic fully-qualified name builder.

``build_fqn`` is the single source of truth for FQN construction. Both the
per-language tree-sitter extractors and the regex fallback call this, so
``symbols.fqn == graph_node.fqn`` for the same logical symbol regardless of
which extraction path produced it. That equality is what lets downstream
joins between the chunk table and a sibling ``code_edges`` table work
without an extra mapping column. Mirrors the recipe from
yonk-full-stack-mig-srv ``indexer/parser.py`` verbatim.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def build_fqn(file_path: str, symbol_name: str, parent_name: Optional[str]) -> str:
    """Compose a dotted fully-qualified name for *symbol_name*.

    The FQN concatenates (a) the last 3 path components of ``file_path``
    with the file extension stripped, (b) ``parent_name`` if present
    (the enclosing class for methods), and (c) ``symbol_name``. Examples::

        build_fqn("/a/b/c.py", "f", None) == "a.b.c.f"
        build_fqn("c.py", "f", None)      == "c.f"
        build_fqn("/a/b/c.py", "g", "C")  == "a.b.c.C.g"

    Three components is enough to disambiguate in practice while keeping the
    FQN short enough to display in a UI. A pathological deep path collapses
    to its last 3 parts — that's intentional. Downstream consumers that need
    a project-relative path should pass the relative path in to this
    function, not the absolute path.
    """
    parts = Path(file_path).parts
    path_prefix = ".".join(parts[-3:]) if len(parts) >= 3 else ".".join(parts)
    # Strip the file extension from the last segment only.
    path_prefix = re.sub(r"\.[^.]+$", "", path_prefix)
    if parent_name:
        return f"{path_prefix}.{parent_name}.{symbol_name}"
    return f"{path_prefix}.{symbol_name}"


__all__ = ["build_fqn"]
