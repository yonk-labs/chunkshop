"""`run_bakeoff(cfg: BakeoffConfig) -> BakeoffResults` (SC-004, SC-005, SC-006).

Serial cross-product over `matrix.embedders x matrix.chunkers`. For each combo:
  1. Build a `CellConfig` and call `runner.run_cell` — reuses the full
     source -> framer -> chunker -> embedder -> sink pipeline.
  2. Count chunks that landed in the combo's table.
Then, once per embedder (not per combo), embed all gold queries in a batch so
N combos sharing an embedder share the same query vectors. For each combo run
pgvector top-K against its table, score with `score.score_query`, aggregate,
and return a `BakeoffResults`.

Design decisions:
- Serial, not parallel. MVP. Parallelism via subprocess orchestrator is an ASK
  FIRST item in the brief.
- DSN env var is read, not passed. Caller (CLI) sets `os.environ[dsn_env] = dsn`.
- Schema creation happens naturally via `run_cell -> sink.create_table -> advisory
  lock`. No pre-creation here.
- Matrix size > 50 is caller's responsibility. CLI checks + prompts; runner
  runs whatever the config says.
"""
from __future__ import annotations

import os
import time
from typing import Any

import numpy as np
import psycopg
from psycopg import sql

from chunkshop.bakeoff.config import (
    BakeoffConfig,
    BakeoffResults,
    ComboResult,
)
from chunkshop.bakeoff.gold import load_gold_queries
from chunkshop.bakeoff.keys import chunker_key, combo_table, embedder_key
from chunkshop.bakeoff.score import aggregate_scores, score_query
from chunkshop.config import (
    CellConfig,
    NoneExtractor,
    RuntimeConfig,
    TargetConfig,
)
from chunkshop.embedders import load_embedder
from chunkshop.runner import run_cell


def _chunker_label(cfg) -> str:
    """Human-readable chunker label for report tables."""
    t = getattr(cfg, "type", type(cfg).__name__)
    if t == "neighbor_expand":
        base = _chunker_label(cfg.base)
        return f"neighbor_expand(window={cfg.window}, base={base})"
    if t == "fixed_overlap":
        return f"fixed_overlap(window_words={cfg.window_words}, step_words={cfg.step_words})"
    return t


def _count_chunks(dsn: str, schema: str, table: str) -> int:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
        )
        return cur.fetchone()[0]


def _query_top_k(
    dsn: str,
    schema: str,
    table: str,
    query_vec: np.ndarray,
    k: int,
) -> list[tuple[str, int]]:
    """pgvector top-K lookup. Returns [(doc_id, seq_num), ...] ordered by cosine distance."""
    vec_str = "[" + ",".join(f"{x:.8f}" for x in query_vec.tolist()) + "]"
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT doc_id, seq_num FROM {}.{} "
                "ORDER BY embedding <=> %s::vector LIMIT %s"
            ).format(sql.Identifier(schema), sql.Identifier(table)),
            (vec_str, k),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def _build_cell_cfg(
    bakeoff: BakeoffConfig,
    chunker_cfg,
    embedder_cfg,
    table: str,
) -> CellConfig:
    """Translate (bakeoff config, one chunker, one embedder) into a runnable CellConfig."""
    rt = bakeoff.runtime or RuntimeConfig()
    return CellConfig(
        cell_name=f"{bakeoff.name}__{chunker_key(chunker_cfg)}__{embedder_key(embedder_cfg)}",
        source=bakeoff.source,
        framer=bakeoff.framer,
        chunker=chunker_cfg,
        embedder=embedder_cfg,
        extractor=NoneExtractor(),
        target=TargetConfig(
            type="postgres",
            dsn_env=bakeoff.target.dsn_env,
            database=bakeoff.target.schema_name,
            table=table,
            mode="overwrite",
            hnsw=False,
        ),
        runtime=rt,
    )


def _corpus_label(bakeoff: BakeoffConfig) -> str:
    """Best-effort human label for the corpus. `files.glob` -> the glob string; else source type."""
    src = bakeoff.source
    if getattr(src, "type", None) == "files":
        return getattr(src, "glob", "files")
    return getattr(src, "type", "unknown")


def run_bakeoff(cfg: BakeoffConfig) -> BakeoffResults:
    """Execute every (chunker, embedder) combo, score against gold, return results.

    Caller must set `os.environ[cfg.target.dsn_env]` before calling — the sink
    reads the DSN from there. Raises `RuntimeError` if the env var is unset.
    """
    if cfg.target.dsn_env not in os.environ:
        raise RuntimeError(
            f"DSN env var {cfg.target.dsn_env!r} is not set. The CLI sets it from "
            "--dsn / $CHUNKSHOP_DSN before calling run_bakeoff."
        )
    dsn = os.environ[cfg.target.dsn_env]
    schema = cfg.target.schema_name

    gold = load_gold_queries(cfg.gold_queries)
    combos_in = [(c, e) for c in cfg.matrix.chunkers for e in cfg.matrix.embedders]

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")

    # ----- Phase 1: ingest every combo serially -----
    ingest_meta: list[dict[str, Any]] = []
    for c, e in combos_in:
        table = combo_table(c, e)
        cell_cfg = _build_cell_cfg(cfg, c, e, table)
        t0 = time.time()
        res = run_cell(cell_cfg)
        wall = time.time() - t0
        if res.error:
            raise RuntimeError(
                f"ingest failed for combo {table}: {res.error}"
            )
        n_chunks = _count_chunks(dsn, schema, table)
        ingest_meta.append(
            {"chunker": c, "embedder": e, "table": table,
             "chunks": n_chunks,
             "wall_seconds": round(wall, 2),
             # Subset of wall_seconds: just the embedder's portion.
             "embed_seconds": round(getattr(res, "embed_seconds", 0.0), 2)}
        )

    # ----- Phase 2: embed gold queries once per embedder (not once per combo) -----
    # Two combos sharing an embedder share the same query vectors.
    # Capture per-embedder query-embed wall time — that's a proxy for
    # production query-time latency at scale.
    query_vecs_by_emb_key: dict[str, np.ndarray] = {}
    query_embed_seconds_by_emb_key: dict[str, float] = {}
    for e in cfg.matrix.embedders:
        k = embedder_key(e)
        if k in query_vecs_by_emb_key:
            continue
        embedder = load_embedder(e)
        t_qe = time.perf_counter()
        vecs = embedder.embed([g.query for g in gold])
        query_embed_seconds_by_emb_key[k] = round(time.perf_counter() - t_qe, 3)
        query_vecs_by_emb_key[k] = vecs

    # ----- Phase 3: score every combo -----
    combo_results: list[ComboResult] = []
    for meta in ingest_meta:
        c = meta["chunker"]
        e = meta["embedder"]
        ck = chunker_key(c)
        ek = embedder_key(e)
        table = meta["table"]
        vecs = query_vecs_by_emb_key[ek]

        per_query: list[dict[str, Any]] = []
        per_query_scores: list[dict[str, float]] = []
        for i, g in enumerate(gold):
            top = _query_top_k(dsn, schema, table, vecs[i], k=cfg.scoring.top_k)
            doc_ids = [t[0] for t in top]
            s = score_query(doc_ids, g.gold_doc_id, cfg.scoring.k)
            per_query_scores.append(s)
            per_query.append({
                "query": g.query,
                "gold_doc_id": g.gold_doc_id,
                "top_k": [{"doc_id": t[0], "seq_num": t[1]} for t in top],
                **s,
            })

        agg = aggregate_scores(per_query_scores)

        combo_results.append(ComboResult(
            chunker_key=ck,
            embedder_key=ek,
            chunker_label=_chunker_label(c),
            embedder_label=e.model_name,
            table=table,
            ingest_chunks=meta["chunks"],
            ingest_wall_seconds=meta["wall_seconds"],
            ingest_embed_seconds=meta["embed_seconds"],
            aggregate=agg,
            per_query=per_query,
        ))

    return BakeoffResults(
        run_name=cfg.name,
        started_at=started_at,
        corpus_label=_corpus_label(cfg),
        n_queries=len(gold),
        n_combos=len(combo_results),
        combos=combo_results,
        gold_queries=[{"query": g.query, "gold_doc_id": g.gold_doc_id} for g in gold],
        query_embed_seconds_by_embedder=query_embed_seconds_by_emb_key,
    )
