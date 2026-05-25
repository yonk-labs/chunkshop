"""Deterministic graph-node ID derivation for code symbols.

``code_symbol_node_id`` mirrors yonk-full-stack-mig-srv's
``graph_persistence.code_symbol_node_id`` so node IDs minted by chunkshop
are stable across re-ingests AND interchangeable with anything downstream
(e.g. pg-raggraph's ``graph_node`` table) that uses the same recipe. The
``project_id`` parameter is what scopes IDs to a particular ingested
codebase — two projects can both contain a symbol named ``find_user``
without their node IDs colliding.

The 16-hex truncation is the same as the source. It gives 64 bits of
collision resistance, which is plenty for any single project, and keeps
the ID short enough to land in URLs and graph viewers without wrapping.
"""
from __future__ import annotations

import hashlib


def code_symbol_node_id(
    project_id: str, language: str, file_path: str, fqn: str
) -> str:
    """Compose ``"node-" + sha1(project_id:language:file_path:fqn)[:16]``.

    Deterministic: same inputs always return the same ID. That property is
    what makes the upsert path in a downstream sink (e.g. ``ON CONFLICT
    (id) DO UPDATE``) idempotent — re-running ingest against the same
    project doesn't multiply rows.
    """
    fqn_full = f"{language}:{file_path}:{fqn}"
    digest = hashlib.sha1(f"{project_id}:{fqn_full}".encode()).hexdigest()[:16]
    return f"node-{digest}"


__all__ = ["code_symbol_node_id"]
