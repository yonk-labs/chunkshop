"""Postgres hybrid retrieval — the read side of chunkshop (Brief B core).

A small, focused read API over a chunkshop chunks table:

  - `ensure_fts`        : idempotently add a generated tsvector column + GIN index
  - `keyword_search`    : full-text search (to_tsquery OR-join + ts_rank)
  - `semantic_search`   : pgvector top-k, distance converted to a comparable
                           higher-is-better score
  - `hybrid_search`     : run multiple legs and fuse (RRF or weighted min-max)

Plus a `where` filter (metadata/source/tags, FILTER-ONLY — not a ranking leg)
shared across every leg.

Security: every user value is a bound parameter. The only non-parameterizable
inputs are the FTS `language` (allowlisted) and SQL identifiers (quoted via the
backend). User query text, filter values, and limits are NEVER interpolated.

Connections are short-lived (one psycopg connection opened per call), mirroring
`PgSink`. This is a read API, so per-call connect is fine.

This module deliberately contains no summary logic — summarization is the
benchmark's job, wired through `chunkshop.summarizers.lede`. (Per the core
import-boundary guard, this file pulls in no summarizer dependency at all.)
"""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Optional

import numpy as np
import psycopg

from chunkshop.backends.postgres import PostgresBackend
# Shared, backend-agnostic primitives. `Hit`, `_fuse_rrf`, `_fuse_weighted` are
# re-exported here for backward compatibility (tests and callers import them as
# `chunkshop.search.Hit` / `chunkshop.search._fuse_rrf`).
from chunkshop.search_common import (  # noqa: F401  (re-exported)
    Hit,
    _fuse_rrf,
    _fuse_weighted,
    fuse as _fuse,
    validate_hybrid_args as _validate_hybrid_args,
)


# FTS regconfig allowlist. The language name is concatenated into the
# generated-column DDL (`to_tsvector('<lang>', ...)`) where it CANNOT be a bound
# parameter, so it must be allowlisted to prevent injection. websearch_to_tsquery
# in the search paths also takes it as a literal for the same reason.
# Covers PostgreSQL's stock text-search configurations (I-4); extend if your
# cluster defines custom ones — keep it an allowlist (never interpolate raw input).
_ALLOWED_LANGUAGES = {
    "simple", "arabic", "armenian", "basque", "catalan", "danish", "dutch",
    "english", "finnish", "french", "german", "greek", "hindi", "hungarian",
    "indonesian", "irish", "italian", "lithuanian", "nepali", "norwegian",
    "portuguese", "romanian", "russian", "serbian", "spanish", "swedish",
    "tamil", "turkish", "yiddish",
}


# Query tokenizer for the FTS leg (I-12). Alphanumeric runs only — punctuation
# and quotes are stripped, so an injection probe like `'; DROP TABLE` tokenizes
# to harmless words. The sanitized tokens are joined with the tsquery OR operator
# (`|`) and bound to `to_tsquery`, so the ONLY operators in the param are the ones
# we insert — injection-safe.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _query_tokens(query: str) -> list[str]:
    """Lowercased alphanumeric tokens, len>=2, deduped preserving order."""
    seen: dict[str, None] = {}
    for tok in _TOKEN_RE.findall(query.lower()):
        if len(tok) >= 2:
            seen.setdefault(tok, None)
    return list(seen)


def _check_language(language: str) -> str:
    if language not in _ALLOWED_LANGUAGES:
        raise ValueError(
            f"language must be one of {sorted(_ALLOWED_LANGUAGES)}, got {language!r} "
            f"(allowlisted because it is concatenated into generated-column DDL)"
        )
    return language


def _backend() -> PostgresBackend:
    # Stateless dialect helper (quote_ident, fq_table, vector_literal). We pass a
    # dummy dsn since we open our own connections from the caller-supplied dsn.
    return PostgresBackend(dsn="")


def _fq(backend: PostgresBackend, schema: str, table: str) -> str:
    return backend.fq_table(schema, table)


# -- read-connection pooling (opt-in) ---------------------------------------
# By default the read path opens a short-lived psycopg connection per call, as
# documented in the module header. For high-QPS callers (a search service
# issuing many queries against one DSN), the ~5-6ms TCP+auth+first-query setup
# per connection dominates latency — measured ~21ms hybrid -> ~8ms with warm
# connections. Setting CHUNKSHOP_SEARCH_POOL=1 routes the hot read legs through
# a tiny thread-safe idle-connection pool keyed by DSN. Connections are opened
# autocommit (reads need no transaction), so nothing lingers idle-in-transaction
# between checkouts. A connection that errors is closed, never returned to the
# pool; a stale/broken connection surfaces as an OperationalError on the next
# query exactly as a fresh connect would. Ranking output is unchanged — this
# only affects how the connection is acquired.
_POOL_LOCK = threading.Lock()
_POOLS: dict[str, list[Any]] = {}
_POOL_MAX_IDLE = 8


def _pool_enabled() -> bool:
    return os.environ.get("CHUNKSHOP_SEARCH_POOL", "").strip().lower() in {"1", "true", "yes", "on"}


def _pool_acquire(dsn: str):
    with _POOL_LOCK:
        idle = _POOLS.get(dsn)
        while idle:
            conn = idle.pop()
            if not conn.closed:
                return conn
    conn = psycopg.connect(dsn)
    conn.autocommit = True
    return conn


def _pool_release(dsn: str, conn, ok: bool) -> None:
    if conn.closed:
        return
    if not ok:
        conn.close()
        return
    with _POOL_LOCK:
        idle = _POOLS.setdefault(dsn, [])
        if len(idle) < _POOL_MAX_IDLE:
            idle.append(conn)
            return
    conn.close()


@contextmanager
def _read_connection(dsn: str):
    """Yield a psycopg connection for a read leg.

    Pooled+reused when CHUNKSHOP_SEARCH_POOL is set; otherwise a plain
    short-lived connection (the historical default), preserving the module's
    documented per-call-connect contract byte-for-byte.
    """
    if not _pool_enabled():
        with psycopg.connect(dsn) as conn:
            yield conn
        return
    conn = _pool_acquire(dsn)
    ok = True
    try:
        yield conn
    except Exception:
        ok = False
        raise
    finally:
        _pool_release(dsn, conn, ok)


def close_search_pool() -> None:
    """Close all idle pooled read connections. Safe to call anytime; idempotent."""
    with _POOL_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for idle in pools:
        for conn in idle:
            try:
                conn.close()
            except Exception:
                pass


# Allowlist regex for column identifiers spliced into ``column_in`` /
# ``column_like`` predicates. Same shape as the PromoteColumn validator —
# lowercase ident, underscores, digits, double-underscore for nested paths.
# The column NAME is interpolated (psycopg cannot bind identifiers); the
# VALUES are always bound params, so the only injection surface is the
# column name itself — gate it with this allowlist.
_COLUMN_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _check_column_ident(name: str) -> None:
    if not _COLUMN_IDENT_RE.match(name):
        raise ValueError(
            f"column name must match ^[a-z_][a-z0-9_]*$, got {name!r} "
            "(allowlisted because column identifiers cannot be bound parameters)"
        )


def _build_where(where: Optional[dict]) -> tuple[str, list[Any]]:
    """Build a parameterized WHERE fragment + params from a filter dict.

    Returns ("", []) when `where` is falsy. The fragment is a conjunction of the
    supported predicates WITHOUT a leading AND/WHERE — callers splice it in.

    Supported keys:
      - {"tags": [...]}            -> tags && ARRAY[...]::text[]   (overlap)
      - {"source": "..."}          -> source = %s
      - {"metadata": {k: v, ...}}  -> metadata @> %s::jsonb        (containment)
      - {"column_in": {col: [v, ...]}}  -> col = ANY(%s::text[])   (one per col)
      - {"column_like": {col: "pat%"}}  -> col LIKE %s             (one per col)

    All values are bound params; none are interpolated. Column NAMES for the
    last two keys are allowlist-validated against ``^[a-z_][a-z0-9_]*$`` so
    they can be safely spliced into the SQL — psycopg cannot bind identifiers.

    CAUTION (I-6): prefer this filter for STRUCTURED fields (source, category,
    tenant, date). Do NOT gate recall on free-text keyword `tags` — query and
    document vocabularies diverge ("rotate API keys" vs a chunk tagged
    [secret, secrets]) and a hard overlap filter silently drops the answer
    (measured: removed the gold doc on ~4/14 queries). Use keyword tags for
    faceting/display or a soft re-rank signal, not a recall-critical WHERE.
    """
    if not where:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []

    unknown = set(where) - {"tags", "source", "metadata", "metadata_not", "column_in", "column_like"}
    if unknown:
        raise ValueError(f"unsupported where keys: {sorted(unknown)}")

    tags = where.get("tags")
    if tags is not None:
        if not isinstance(tags, (list, tuple)):
            raise ValueError("where['tags'] must be a list of strings")
        # I-7: case-insensitive overlap. Stored tags are lowercased at ingest
        # (lede tags are lowercase), so casefolding only the query-side values is
        # sufficient — cheaper than wrapping the column in lower(tags).
        # Still a bound param: we lowercase the Python values, never interpolate.
        # array overlap: ARRAY[%s, %s, ...]::text[]
        placeholders = ", ".join(["%s"] * len(tags))
        clauses.append(f"tags && ARRAY[{placeholders}]::text[]")
        params.extend([str(t).casefold() for t in tags])

    source = where.get("source")
    if source is not None:
        clauses.append("source = %s")
        params.append(source)

    meta = where.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            raise ValueError("where['metadata'] must be a dict")
        clauses.append("metadata @> %s::jsonb")
        params.append(json.dumps(meta))

    # `metadata_not`: exclude rows whose metadata key EQUALS the given value.
    # Uses `IS DISTINCT FROM` (not `<>`) so rows that LACK the key (jsonb ->> is
    # NULL) are KEPT — making this a no-op for plain chunk rows that carry no
    # such key. The metadata KEY is a bound `%s` param, never interpolated.
    # NOTE: `->>` extracts jsonb as TEXT, so the value is compared as text —
    # intended for string-valued keys (e.g. kind='fact'). A bool/number value
    # would compare against its text form, which may not match as expected.
    meta_not = where.get("metadata_not")
    if meta_not is not None:
        if not isinstance(meta_not, dict):
            raise ValueError("where['metadata_not'] must be a dict")
        for key, value in meta_not.items():
            clauses.append("(metadata ->> %s) IS DISTINCT FROM %s")
            params.extend([key, value])

    column_in = where.get("column_in")
    if column_in is not None:
        if not isinstance(column_in, dict):
            raise ValueError("where['column_in'] must be a dict[str, list]")
        for col, values in column_in.items():
            _check_column_ident(col)
            if not isinstance(values, (list, tuple)) or not values:
                raise ValueError(
                    f"where['column_in'][{col!r}] must be a non-empty list"
                )
            clauses.append(f"{col} = ANY(%s::text[])")
            # ANY() takes a Postgres array — psycopg adapts a Python list.
            params.append([str(v) for v in values])

    column_like = where.get("column_like")
    if column_like is not None:
        if not isinstance(column_like, dict):
            raise ValueError("where['column_like'] must be a dict[str, str]")
        for col, pattern in column_like.items():
            _check_column_ident(col)
            if not isinstance(pattern, str):
                raise ValueError(
                    f"where['column_like'][{col!r}] must be a string LIKE pattern"
                )
            clauses.append(f"{col} LIKE %s")
            params.append(pattern)

    return " AND ".join(clauses), params


def ensure_fts(
    dsn: str,
    *,
    schema: str,
    table: str,
    language: str = "english",
    include_metadata_paths: list[str] | None = None,
) -> None:
    """Idempotently add a generated `search_vector` tsvector column + GIN index.

    Safe to call repeatedly. The tsvector is GENERATED ALWAYS from
    original_content plus any configured metadata paths, so it stays in sync
    with no application-side maintenance.
    """
    _check_language(language)
    metadata_paths = include_metadata_paths or []
    for path in metadata_paths:
        if not path or not all(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", seg) for seg in path.split(".")):
            raise ValueError(
                "include_metadata_paths segments must match "
                f"^[A-Za-z_][A-Za-z0-9_]*$ separated by '.', got {path!r}"
            )
    backend = _backend()
    fq = _fq(backend, schema, table)
    idx = backend.quote_ident(f"{table}_fts_idx")
    text_parts = ["original_content"]
    for path in metadata_paths:
        pg_path = ",".join(path.split("."))
        text_parts.append(f"COALESCE(metadata #>> '{{{pg_path}}}', '')")
    source_text = " || ' ' || ".join(text_parts)

    add_col = (
        f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS search_vector tsvector "
        f"GENERATED ALWAYS AS (to_tsvector('{language}', {source_text})) STORED"
    )
    create_idx = f"CREATE INDEX IF NOT EXISTS {idx} ON {fq} USING GIN(search_vector)"

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(add_col)
        cur.execute(create_idx)
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
    """Full-text search via to_tsquery (OR semantics) + ts_rank. legs=('fts',).

    I-12: tokens are joined with the tsquery OR operator (` | `) so partial-term
    matches count — a multi-word query matches docs containing ANY token, not
    only docs containing ALL of them (the AND default of websearch_to_tsquery).
    """
    _check_language(language)
    tokens = _query_tokens(query)
    if not tokens:
        # No usable tokens (empty / all-punctuation / all single-char) — an empty
        # tsquery errors, so short-circuit to "no FTS hits".
        return []
    or_tsquery = " | ".join(tokens)
    backend = _backend()
    fq = _fq(backend, schema, table)
    where_sql, where_params = _build_where(where)

    sql = (
        f"SELECT doc_id, seq_num, original_content, metadata, "
        f"ts_rank(search_vector, to_tsquery('{language}', %s)) AS score, "
        f"embedded_content "
        f"FROM {fq} "
        f"WHERE search_vector @@ to_tsquery('{language}', %s)"
    )
    params: list[Any] = [or_tsquery, or_tsquery]
    if where_sql:
        sql += f" AND {where_sql}"
        params.extend(where_params)
    sql += " ORDER BY score DESC LIMIT %s"
    params.append(k)

    with _read_connection(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Hit(
            doc_id=r[0],
            seq_num=r[1],
            text=r[2],
            metadata=r[3] or {},
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
    vector_metric: str = "cosine",
) -> list[Hit]:
    """pgvector top-k; score is higher-is-better. legs=('semantic',).

    ``vector_metric`` selects the pgvector operator:

    - ``"cosine"`` -> ``<=>`` distance, score = ``1 - distance``
    - ``"inner_product"`` -> ``<#>`` negative inner product, score = inner product
    - ``"l2"`` -> ``<->`` Euclidean distance, score = ``-distance``
    """
    backend = _backend()
    op, _opclass = backend.vector_metric_sql(vector_metric)
    fq = _fq(backend, schema, table)
    vec_lit = backend.vector_literal(query_vec)
    where_sql, where_params = _build_where(where)

    sql = (
        f"SELECT doc_id, seq_num, original_content, metadata, "
        f"embedding {op} %s::vector AS distance, "
        f"embedded_content "
        f"FROM {fq}"
    )
    params: list[Any] = [vec_lit]
    if where_sql:
        sql += f" WHERE {where_sql}"
        params.extend(where_params)
    sql += f" ORDER BY embedding {op} %s::vector LIMIT %s"
    params.extend([vec_lit, k])

    with _read_connection(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        Hit(
            doc_id=r[0],
            seq_num=r[1],
            text=r[2],
            metadata=r[3] or {},
            score=backend.vector_score(float(r[4]), vector_metric),
            legs=("semantic",),
            embedded_text=r[5] or "",
        )
        for r in rows
    ]


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
    vector_metric: str = "cosine",
) -> list[Hit]:
    """Run the requested legs and fuse them into a single ranked list.

    legs: subset of ("semantic", "fts"). "semantic" requires query_vec; "fts"
    requires query.

    I-3: each leg fetches `max(k * candidate_multiplier, k)` candidates rather
    than just `k`, so a chunk that ranks > k within an individual leg but would
    land in the fused top-k still enters the candidate pool. The fused result is
    truncated to `k` at the end.

    fusion="rrf": each leg contributes 1/(rrf_k + rank); rows hit by multiple
        legs sum their contributions and thus rank higher (the point of hybrid).
    fusion="weighted": min-max normalize each leg's scores to [0,1], then
        sum(weights[leg] * norm_score); default weight 1.0 per leg.

    Returns top-k by fused score. Each Hit's `legs` lists which legs matched it.
    """
    _validate_hybrid_args(legs=legs, query=query, query_vec=query_vec, fusion=fusion)

    # Over-fetch per leg so deep-but-dual-matched candidates survive into fusion.
    cand_k = max(k * candidate_multiplier, k)

    # Each leg is an independent, side-effect-free SELECT on its own short-lived
    # connection, so the legs can run concurrently. psycopg releases the GIL
    # while waiting on the server, so two legs on two backends overlap in real
    # time — a 2-leg hybrid drops from sum(legs) to ~max(legs) wall time. Fusion
    # consumes the same per-leg results regardless of execution order, so the
    # ranked output is identical to the sequential path (no accuracy change).
    leg_thunks: dict[str, Any] = {}
    if "semantic" in legs:
        leg_thunks["semantic"] = lambda: semantic_search(
            dsn, schema=schema, table=table, query_vec=query_vec, k=cand_k,
            where=where, vector_metric=vector_metric,
        )
    if "fts" in legs:
        leg_thunks["fts"] = lambda: keyword_search(
            dsn, schema=schema, table=table, query=query, k=cand_k, where=where,
            language=language,
        )

    leg_results: dict[str, list[Hit]] = {}
    if len(leg_thunks) > 1:
        # Spawn one worker per extra leg; the call thread runs none idle. Thread
        # spawn (~0.1ms) is negligible against per-leg query latency (ms-scale).
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=len(leg_thunks)) as pool:
            futures = {name: pool.submit(thunk) for name, thunk in leg_thunks.items()}
            # .result() re-raises any leg exception, matching sequential behavior.
            for name, fut in futures.items():
                leg_results[name] = fut.result()
    else:
        for name, thunk in leg_thunks.items():
            leg_results[name] = thunk()

    return _fuse(leg_results, k=k, fusion=fusion, weights=weights, rrf_k=rrf_k)
