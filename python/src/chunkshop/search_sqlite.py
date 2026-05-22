"""SQLite hybrid retrieval — FTS5 keyword + sqlite-vec semantic + fusion.

Mirrors `chunkshop.search` (Postgres) on SQLite, returning the SAME `Hit`
contract and reusing the SAME fusion helpers (`chunkshop.search_common`).

Layout recap (see `chunkshop.sinks.sqlite`): the chunks live in `<table>` and the
embeddings in a `vec0` virtual table `<table>_vec` joined on `id` (= `doc_id::seq_num`).
This module adds a third sibling, `<table>_fts`, an external-content FTS5 table over
`<table>.original_content` keyed by the chunks table rowid.

Legs:
  - "fts"      : `<table>_fts MATCH ?` ranked by `bm25()` (negative-better; we
                 negate to higher-better).
  - "semantic" : sqlite-vec `embedding MATCH ? AND k = ?`, distance → 1-distance.

Security: the FTS query, vector literal, filter values, and k are all bound
parameters. Identifiers (table names) are quoted via the backend. SQLite has no
schema namespace, so the `schema` arg is accepted-and-ignored for API parity
with the Postgres module.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import numpy as np

from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.search_common import Hit, fuse, validate_hybrid_args


# Query tokenizer for the FTS leg (I-12). Alphanumeric runs only — punctuation
# and quotes are stripped, so an injection probe tokenizes to harmless words. The
# sanitized tokens are joined with the FTS5 OR operator and bound to `MATCH ?`.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _query_tokens(query: str) -> list[str]:
    """Lowercased alphanumeric tokens, len>=2, deduped preserving order."""
    seen: dict[str, None] = {}
    for tok in _TOKEN_RE.findall(query.lower()):
        if len(tok) >= 2:
            seen.setdefault(tok, None)
    return list(seen)


def _backend(dsn: str) -> SQLiteBackend:
    return SQLiteBackend(dsn=dsn)


def _q(backend: SQLiteBackend, name: str) -> str:
    return backend.quote_ident(name)


def _build_where(where: Optional[dict]) -> tuple[str, list[Any]]:
    """Parameterized WHERE fragment (no leading AND) + params, over the `c` alias.

    Supported keys:
      - {"source": "..."}          -> c.source = ?
      - {"tags": [...]}            -> EXISTS json_each(c.tags) overlap (case-insensitive)
      - {"metadata": {k: v, ...}}  -> json_extract(c.metadata, '$.k') = ? per key

    Unlike Postgres there is no jsonb-containment operator; metadata containment
    is approximated by per-key equality on top-level keys, which covers the flat
    {"topic": "..."} metadata chunkshop writes. Nested containment is not
    supported (documented gap).
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
        clauses.append("c.source = ?")
        params.append(source)

    tags = where.get("tags")
    if tags is not None:
        if not isinstance(tags, (list, tuple)):
            raise ValueError("where['tags'] must be a list of strings")
        # tags stored as a JSON array of (lowercased) strings. Overlap = any
        # query tag present. Case-insensitive: query tags casefolded, stored
        # tags lowered via json_each + lower().
        placeholders = ", ".join(["?"] * len(tags))
        clauses.append(
            f"EXISTS (SELECT 1 FROM json_each(c.tags) je "
            f"WHERE lower(je.value) IN ({placeholders}))"
        )
        params.extend([str(t).casefold() for t in tags])

    meta = where.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            raise ValueError("where['metadata'] must be a dict")
        for key, val in meta.items():
            # json_extract path is a single top-level key; bind the value.
            clauses.append(f"json_extract(c.metadata, '$.' || ?) = ?")
            params.extend([key, val])

    return " AND ".join(clauses), params


def ensure_fts(
    dsn: str,
    *,
    schema: str = "",
    table: str,
    language: str = "english",
) -> None:
    """Idempotently create+populate an external-content FTS5 table over original_content.

    `<table>_fts` is content='<table>', content_rowid='rowid'. We drop+recreate
    its contents via the 'rebuild' command each call so it stays in sync with the
    base table without per-write triggers (this is a read-side helper invoked
    after ingest, not on the hot write path).

    `schema`/`language` are accepted for API parity with the Postgres module;
    FTS5 tokenization is language-agnostic (unicode61), so `language` is ignored.
    """
    del schema, language
    backend = _backend(dsn)
    fts = _q(backend, f"{table}_fts")
    base = _q(backend, table)
    with backend.connect() as conn:
        cur = conn.cursor()
        # external-content FTS5: indexes original_content, content stays in base.
        cur.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5("
            f"original_content, content={base}, content_rowid='rowid')"
        )
        # Rebuild from content table — idempotent, picks up all current rows.
        cur.execute(f"INSERT INTO {fts}({fts}) VALUES('rebuild')")
        conn.commit()


def keyword_search(
    dsn: str,
    *,
    schema: str = "",
    table: str,
    query: str,
    k: int,
    where: Optional[dict] = None,
    language: str = "english",
) -> list[Hit]:
    """FTS5 MATCH (OR semantics) ranked by bm25 (negated to higher-better). legs=('fts',).

    I-12: tokens are joined with the FTS5 `OR` operator so partial-term matches
    count — a multi-word query matches docs containing ANY token, not only docs
    containing ALL of them (FTS5's implicit-AND default for a bare query string).
    """
    del schema, language
    tokens = _query_tokens(query)
    if not tokens:
        # No usable tokens — return no FTS hits rather than running an empty MATCH.
        return []
    or_match = " OR ".join(tokens)
    backend = _backend(dsn)
    fts = _q(backend, f"{table}_fts")
    base = _q(backend, table)
    where_sql, where_params = _build_where(where)

    # bm25() is lower(=more negative)-is-better; negate so higher=better, matching
    # the Hit contract across backends. Join FTS rowid back to the base table.
    sql = (
        f"SELECT c.doc_id, c.seq_num, c.original_content, c.metadata, "
        f"-bm25({fts}) AS score, c.embedded_content "
        f"FROM {fts} f JOIN {base} c ON c.rowid = f.rowid "
        f"WHERE {fts} MATCH ?"
    )
    params: list[Any] = [or_match]
    if where_sql:
        sql += f" AND {where_sql}"
        params.extend(where_params)
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(k)

    with backend.connect() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
        except Exception:
            # FTS5 raises on malformed MATCH expressions (e.g. a bare quote from
            # an injection probe). Treat as "no FTS hits" rather than propagating
            # — mirrors PG's websearch_to_tsquery tolerance of junk input.
            return []
        rows = cur.fetchall()
    return [
        Hit(
            doc_id=r[0],
            seq_num=r[1],
            text=r[2],
            metadata=json.loads(r[3]) if r[3] else {},
            score=float(r[4]),
            legs=("fts",),
            embedded_text=r[5] or "",
        )
        for r in rows
    ]


def semantic_search(
    dsn: str,
    *,
    schema: str = "",
    table: str,
    query_vec: np.ndarray,
    k: int,
    where: Optional[dict] = None,
) -> list[Hit]:
    """sqlite-vec KNN; score = 1 - distance (higher=better). legs=('semantic',).

    A `where` filter requires post-filtering: sqlite-vec's `embedding MATCH ?
    AND k = ?` does not accept arbitrary WHERE predicates inside the vec table
    scan. We over-fetch (k * 4, capped) from the vec table, join to the base
    table, apply the filter there, then truncate to k. The over-fetch keeps the
    filtered top-k stable for the small/medium tables this targets.
    """
    backend = _backend(dsn)
    vec = _q(backend, f"{table}_vec")
    base = _q(backend, table)
    vec_lit = backend.vector_literal(query_vec)  # JSON array string
    where_sql, where_params = _build_where(where)

    fetch_k = k if not where_sql else max(k * 4, k)
    sql = (
        f"SELECT c.doc_id, c.seq_num, c.original_content, c.metadata, v.distance, "
        f"c.embedded_content "
        f"FROM {vec} v JOIN {base} c ON c.id = v.id "
        f"WHERE v.embedding MATCH ? AND k = ?"
    )
    params: list[Any] = [vec_lit, fetch_k]
    if where_sql:
        sql += f" AND {where_sql}"
        params.extend(where_params)
    sql += " ORDER BY v.distance"

    with backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
    hits = [
        Hit(
            doc_id=r[0],
            seq_num=r[1],
            text=r[2],
            metadata=json.loads(r[3]) if r[3] else {},
            score=1.0 - float(r[4]),
            legs=("semantic",),
            embedded_text=r[5] or "",
        )
        for r in rows
    ]
    return hits[:k]


def hybrid_search(
    dsn: str,
    *,
    schema: str = "",
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
