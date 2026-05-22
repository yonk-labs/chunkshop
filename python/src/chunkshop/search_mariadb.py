"""MariaDB hybrid retrieval — FULLTEXT keyword + VECTOR semantic + fusion.

Mirrors `chunkshop.search` (Postgres) on MariaDB 11.7+, returning the SAME `Hit`
contract and reusing the SAME fusion helpers (`chunkshop.search_common`).

Legs:
  - "fts"      : `MATCH(original_content) AGAINST(? IN NATURAL LANGUAGE MODE)`,
                 ranked by the relevance score AGAINST returns (higher=better).
  - "semantic" : `VEC_DISTANCE_COSINE(embedding, VEC_FromText(...))`, score =
                 1 - distance (higher=better), matching the other backends.

FULLTEXT requires an index on original_content (`ensure_fts` adds it). MariaDB's
FULLTEXT works on InnoDB (the chunks table's engine).

Security: the query text, filter values, and k are bound parameters (PyMySQL
%s). The vector literal is composed inline via the backend's `vector_literal`
(`VEC_FromText('[…]')`) — same pattern the sink uses, since MariaDB has no
vector parameter adapter; the float values come from a numpy array, never from
user text. Identifiers are quoted via the backend.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import numpy as np

from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.search_common import Hit, fuse, validate_hybrid_args


def _backend(dsn: str) -> MariaDBBackend:
    return MariaDBBackend(dsn=dsn)


def _fq(backend: MariaDBBackend, db: str, table: str) -> str:
    return backend.fq_table(db, table)


def _build_where(where: Optional[dict]) -> tuple[str, list[Any]]:
    """Parameterized WHERE fragment (no leading AND) + params.

    Supported keys:
      - {"source": "..."}          -> source = %s
      - {"tags": [...]}            -> JSON_OVERLAPS(tags, %s) (case-insensitive on
                                      lowercased query tags vs lowercased stored)
      - {"metadata": {k: v, ...}}  -> JSON_EXTRACT(metadata, '$.k') = %s per key

    metadata containment is approximated by per-key equality on top-level keys,
    matching SQLite's behavior (documented gap: no nested jsonb containment).
    """
    if not where:
        return "", []
    unknown = set(where) - {"tags", "source", "metadata"}
    if unknown:
        raise ValueError(f"unsupported where keys: {sorted(unknown)}")

    clauses: list[str] = []
    params: list[Any] = []

    source = where.get("source")
    if source is not None:
        clauses.append("source = %s")
        params.append(source)

    tags = where.get("tags")
    if tags is not None:
        if not isinstance(tags, (list, tuple)):
            raise ValueError("where['tags'] must be a list of strings")
        # Stored tags are a JSON array of lowercased strings. JSON_OVERLAPS
        # returns 1 if any element matches. Casefold the query side; the value
        # is a single bound JSON-array parameter.
        lowered = [str(t).casefold() for t in tags]
        clauses.append("JSON_OVERLAPS(tags, %s)")
        params.append(json.dumps(lowered))

    meta = where.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            raise ValueError("where['metadata'] must be a dict")
        for key, val in meta.items():
            # JSON path built from a bound key via JSON_EXTRACT(col, CONCAT('$.', %s)).
            clauses.append("JSON_UNQUOTE(JSON_EXTRACT(metadata, CONCAT('$.', %s))) = %s")
            params.extend([key, str(val)])

    return " AND ".join(clauses), params


def _fulltext_index_exists(cur, db: str, table: str, index_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.STATISTICS "
        "WHERE table_schema=%s AND table_name=%s AND index_name=%s LIMIT 1",
        (db, table, index_name),
    )
    return cur.fetchone() is not None


def ensure_fts(
    dsn: str,
    *,
    schema: str,
    table: str,
    language: str = "english",
) -> None:
    """Idempotently add a FULLTEXT index on original_content.

    MariaDB has no `ADD FULLTEXT INDEX IF NOT EXISTS`, so we introspect
    information_schema.STATISTICS first. `language` is accepted for API parity
    (MariaDB FULLTEXT tokenization is built-in, not language-parameterized).
    """
    del language
    backend = _backend(dsn)
    fq = _fq(backend, schema, table)
    index_name = f"{table}_ft"
    with backend.connect() as conn:
        cur = conn.cursor()
        if not _fulltext_index_exists(cur, schema, table, index_name):
            cur.execute(
                f"ALTER TABLE {fq} ADD FULLTEXT INDEX "
                f"{backend.quote_ident(index_name)} (original_content)"
            )
            conn.commit()


def keyword_search(
    dsn: str,
    *,
    schema: str,
    table: str,
    query: str,
    k: int,
    where: Optional[dict] = None,
    language: str = "english",
) -> list[Hit]:
    """FULLTEXT NATURAL LANGUAGE MODE; score = AGAINST relevance. legs=('fts',)."""
    del language
    backend = _backend(dsn)
    fq = _fq(backend, schema, table)
    where_sql, where_params = _build_where(where)

    # I-12: NATURAL LANGUAGE MODE is OR-by-design — a doc matches if it contains
    # ANY query word, so multi-word queries don't require all terms to co-occur.
    # No change needed here (unlike the pg/sqlite AND defaults).
    sql = (
        f"SELECT doc_id, seq_num, original_content, metadata, "
        f"MATCH(original_content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score, "
        f"embedded_content "
        f"FROM {fq} "
        f"WHERE MATCH(original_content) AGAINST(%s IN NATURAL LANGUAGE MODE)"
    )
    params: list[Any] = [query, query]
    if where_sql:
        sql += f" AND {where_sql}"
        params.extend(where_params)
    sql += " ORDER BY score DESC LIMIT %s"
    params.append(k)

    with backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Hit(
            doc_id=r[0],
            seq_num=r[1],
            text=r[2],
            metadata=_load_meta(r[3]),
            score=float(r[4]),
            legs=("fts",),
            embedded_text=r[5] or "",
        )
        for r in rows
    ]


def semantic_search(
    dsn: str,
    *,
    schema: str,
    table: str,
    query_vec: np.ndarray,
    k: int,
    where: Optional[dict] = None,
) -> list[Hit]:
    """VEC_DISTANCE_COSINE top-k; score = 1 - distance. legs=('semantic',)."""
    backend = _backend(dsn)
    fq = _fq(backend, schema, table)
    vec_expr = backend.vector_literal(query_vec)  # "VEC_FromText('[…]')" — inline literal
    where_sql, where_params = _build_where(where)

    sql = (
        f"SELECT doc_id, seq_num, original_content, metadata, "
        f"VEC_DISTANCE_COSINE(embedding, {vec_expr}) AS distance, "
        f"embedded_content "
        f"FROM {fq}"
    )
    params: list[Any] = []
    if where_sql:
        sql += f" WHERE {where_sql}"
        params.extend(where_params)
    sql += " ORDER BY distance LIMIT %s"
    params.append(k)

    with backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Hit(
            doc_id=r[0],
            seq_num=r[1],
            text=r[2],
            metadata=_load_meta(r[3]),
            score=1.0 - float(r[4]),
            legs=("semantic",),
            embedded_text=r[5] or "",
        )
        for r in rows
    ]


def _load_meta(raw: Any) -> dict:
    """metadata column is JSON; PyMySQL may hand it back as str or already-parsed."""
    if raw is None:
        return {}
    if isinstance(raw, (dict, list)):
        return raw if isinstance(raw, dict) else {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def hybrid_search(
    dsn: str,
    *,
    schema: str,
    table: str,
    query: Optional[str] = None,
    query_vec: Optional[np.ndarray] = None,
    k: int,
    legs: tuple[str, ...] = ("semantic", "fts"),
    where: Optional[dict] = None,
    fusion: str = "rrf",
    weights: Optional[dict[str, float]] = None,
    rrf_k: int = 60,
    language: str = "english",
    candidate_multiplier: int = 3,
) -> list[Hit]:
    """Run the requested legs and fuse — identical fusion semantics to Postgres."""
    validate_hybrid_args(legs=legs, query=query, query_vec=query_vec, fusion=fusion)
    cand_k = max(k * candidate_multiplier, k)

    leg_results: dict[str, list[Hit]] = {}
    if "semantic" in legs:
        leg_results["semantic"] = semantic_search(
            dsn, schema=schema, table=table, query_vec=query_vec, k=cand_k, where=where
        )
    if "fts" in legs:
        leg_results["fts"] = keyword_search(
            dsn, schema=schema, table=table, query=query, k=cand_k, where=where,
        )
    return fuse(leg_results, k=k, fusion=fusion, weights=weights, rrf_k=rrf_k)
