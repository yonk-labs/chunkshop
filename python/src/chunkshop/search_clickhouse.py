"""ClickHouse hybrid retrieval — DEGRADED token-filter keyword + vector semantic + fusion.

Mirrors `chunkshop.search` (Postgres) on ClickHouse, returning the SAME `Hit`
contract and reusing the SAME fusion helpers (`chunkshop.search_common`).

IMPORTANT — the FTS leg is intentionally DEGRADED (filter-grade, not ranked):
ClickHouse has no general-purpose ranked full-text search comparable to
Postgres `ts_rank` / FTS5 `bm25` / MariaDB FULLTEXT relevance. We use token
matching (`multiSearchAnyCaseInsensitive` over the query's whitespace tokens),
optionally accelerated by a `tokenbf_v1` data-skipping index. A document either
matches (any query token present) or it doesn't — the keyword score is a
constant 1.0 for every match. This is by design (see CLAUDE.md / LD-3); don't
try to force ranked FTS ClickHouse can't do well.

Consequences for fusion:
  - RRF: every fts match shares the same rank-1 contribution from the fts leg
    (ties), so the semantic leg breaks ties among fts matches. A doc hit by BOTH
    legs still outranks a doc hit by only one (the point of hybrid) — the fts
    leg contributes match/no-match, not graded relevance.
  - weighted: every fts match normalizes to the same value (span=0 → norm=1.0).

Legs:
  - "fts"      : `multiSearchAnyCaseInsensitive(original_content, [tokens])`, score 1.0.
  - "semantic" : `cosineDistance(embedding, [...])`, score = 1 - distance.

Security: query tokens, filter values, and k are bound parameters
(clickhouse-connect `{name:Type}` style). Identifiers quoted via the backend.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import numpy as np

from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.search_common import Hit, fuse, validate_hybrid_args

# Score assigned to every keyword (token-filter) match. Binary, not ranked.
_FTS_MATCH_SCORE = 1.0

# Tokenizer for the degraded FTS leg: alphanumeric runs, lowercased. Matches the
# token granularity tokenbf_v1 indexes on. Punctuation/quotes are stripped, so
# an injection probe like `'; DROP TABLE` simply tokenizes to ['drop','table'].
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(query: str) -> list[str]:
    return _TOKEN_RE.findall(query.lower())


def _backend(dsn: str) -> ClickHouseBackend:
    return ClickHouseBackend(dsn=dsn)


def _fq(backend: ClickHouseBackend, db: str, table: str) -> str:
    return backend.fq_table(db, table)


def _build_where(where: Optional[dict]) -> tuple[str, dict]:
    """Parameterized WHERE fragment (no leading AND) + a params dict.

    Supported keys:
      - {"source": "..."}          -> source = {flt_source:String}
      - {"tags": [...]}            -> hasAny(tags, {flt_tags:Array(String)})
      - {"metadata": {k: v}}       -> JSONExtractString(metadata, 'k') = {…} per key

    metadata containment is per-key equality on top-level keys (documented gap:
    no nested containment), matching the SQLite/MariaDB modules. The metadata
    key itself is a JSON path segment that CANNOT be a bound parameter in
    JSONExtractString, so it is allowlisted to a safe identifier charset.
    """
    if not where:
        return "", {}
    unknown = set(where) - {"tags", "source", "metadata"}
    if unknown:
        raise ValueError(f"unsupported where keys: {sorted(unknown)}")

    clauses: list[str] = []
    params: dict[str, Any] = {}

    source = where.get("source")
    if source is not None:
        clauses.append("source = {flt_source:String}")
        params["flt_source"] = source

    tags = where.get("tags")
    if tags is not None:
        if not isinstance(tags, (list, tuple)):
            raise ValueError("where['tags'] must be a list of strings")
        clauses.append("hasAny(tags, {flt_tags:Array(String)})")
        params["flt_tags"] = [str(t).casefold() for t in tags]

    meta = where.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            raise ValueError("where['metadata'] must be a dict")
        for i, (key, val) in enumerate(meta.items()):
            safe_key = _safe_json_key(key)
            pname = f"flt_meta_{i}"
            clauses.append(f"JSONExtractString(metadata, '{safe_key}') = {{{pname}:String}}")
            params[pname] = str(val)

    return " AND ".join(clauses), params


_SAFE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_json_key(key: str) -> str:
    """Allowlist a metadata key used as a JSON path segment (cannot be bound).

    Mirrors the identifier-safety stance elsewhere in chunkshop: rather than
    widen quoting, we refuse anything outside a conservative ident charset.
    """
    if not _SAFE_KEY_RE.match(key):
        raise ValueError(
            f"metadata filter key {key!r} is not a safe identifier "
            f"(must match {_SAFE_KEY_RE.pattern})"
        )
    return key


def ensure_fts(
    dsn: str,
    *,
    schema: str,
    table: str,
    language: str = "english",
) -> None:
    """Idempotently add a tokenbf_v1 data-skipping index on original_content.

    The index only ACCELERATES token matching; correctness does not depend on
    it (multiSearchAny works without it). `ADD INDEX IF NOT EXISTS` is idempotent
    on CH. After adding, MATERIALIZE so existing parts get indexed. `language` is
    accepted for API parity (CH tokenization is whitespace/non-alnum based).
    """
    del language
    backend = _backend(dsn)
    fq = _fq(backend, schema, table)
    with backend.connect() as client:
        client.command(
            f"ALTER TABLE {fq} ADD INDEX IF NOT EXISTS "
            f"original_content_tok original_content TYPE tokenbf_v1(8192, 3, 0) GRANULARITY 1"
        )
        # Materialize the index over existing parts (no-op if already built /
        # empty). Wrapped in try: on a brand-new empty table this is a cheap
        # no-op, but some CH builds error materializing an index with zero parts.
        try:
            client.command(f"ALTER TABLE {fq} MATERIALIZE INDEX original_content_tok")
        except Exception:
            pass


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
    """DEGRADED token-filter match. Binary score 1.0 per match. legs=('fts',).

    A document matches if ANY query token appears in original_content
    (case-insensitive). Score is a constant — this leg contributes match/no-match
    to fusion, not graded relevance (see module docstring).
    """
    del language
    toks = _tokens(query)
    if not toks:
        return []
    backend = _backend(dsn)
    fq = _fq(backend, schema, table)
    where_sql, where_params = _build_where(where)

    # I-12: multiSearchAnyCaseInsensitive is OR-by-design — a doc matches if ANY
    # token is present, so multi-word queries don't require all terms to co-occur.
    # No change needed here (unlike the pg/sqlite AND defaults).
    params: dict[str, Any] = {"toks": toks, "k": k, **where_params}
    sql = (
        f"SELECT doc_id, seq_num, original_content, metadata, embedded_content "
        f"FROM {fq} "
        f"WHERE multiSearchAnyCaseInsensitive(original_content, {{toks:Array(String)}})"
    )
    if where_sql:
        sql += f" AND {where_sql}"
    sql += " LIMIT {k:UInt32}"

    with backend.connect() as client:
        r = client.query(sql, parameters=params)
        rows = r.result_rows
    return [
        Hit(
            doc_id=row[0],
            seq_num=int(row[1]),
            text=row[2],
            metadata=_load_meta(row[3]),
            score=_FTS_MATCH_SCORE,
            legs=("fts",),
            embedded_text=row[4] or "",
        )
        for row in rows
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
    """cosineDistance top-k; score = 1 - distance. legs=('semantic',)."""
    backend = _backend(dsn)
    fq = _fq(backend, schema, table)
    vec = backend.vector_literal(query_vec)  # list[float] — bound param
    where_sql, where_params = _build_where(where)

    params: dict[str, Any] = {"q": vec, "k": k, **where_params}
    sql = (
        f"SELECT doc_id, seq_num, original_content, metadata, "
        f"cosineDistance(embedding, {{q:Array(Float32)}}) AS dist, "
        f"embedded_content "
        f"FROM {fq}"
    )
    if where_sql:
        sql += f" WHERE {where_sql}"
    sql += " ORDER BY dist LIMIT {k:UInt32}"

    with backend.connect() as client:
        r = client.query(sql, parameters=params)
        rows = r.result_rows
    return [
        Hit(
            doc_id=row[0],
            seq_num=int(row[1]),
            text=row[2],
            metadata=_load_meta(row[3]),
            score=1.0 - float(row[4]),
            legs=("semantic",),
            embedded_text=row[5] or "",
        )
        for row in rows
    ]


def _load_meta(raw: Any) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
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
    """Run the requested legs and fuse — identical fusion semantics to Postgres.

    NB: the fts leg is binary (every match scores 1.0), so within the fts leg all
    matches tie at rank 1 for RRF purposes in insertion order. The semantic leg
    provides the graded signal; dual-leg matches still outrank single-leg ones.
    """
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
