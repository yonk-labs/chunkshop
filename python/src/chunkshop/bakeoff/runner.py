"""`run_bakeoff(cfg: BakeoffConfig) -> BakeoffResults` (multi-backend).

Outer-loop over `cfg.targets` × inner-loop over `matrix.chunkers × matrix.embedders`.
For each (backend, chunker, embedder) cell:
  1. Build a `CellConfig` and call `runner.run_cell` — reuses the full
     source -> framer -> chunker -> embedder -> sink pipeline.
  2. After every cell ingests, embed gold queries once per embedder.
  3. For every cell, build a Sink, call `query_top_k` for each gold query,
     score with `score.score_query`, aggregate, return.

The runner does not own connection cleanup — the CLI (or the user) is
responsible for dropping schemas / databases / files after the run.

Design decisions:
- Serial, not parallel. Each cell ingests in order; per-backend phasing is
  outer (so one backend finishes before the next starts).
- DSN env vars are read, not passed. Caller must export them before running.
- Schema/database creation happens naturally via `run_cell -> sink.create_table`.
"""
from __future__ import annotations

import os
import time
from typing import Any

import numpy as np

from chunkshop.bakeoff.config import (
    BakeoffConfig,
    BakeoffResults,
    BakeoffTarget,
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
from chunkshop.sinks import load_sink


def _chunker_label(cfg) -> str:
    """Human-readable chunker label for report tables."""
    t = getattr(cfg, "type", type(cfg).__name__)
    if t == "neighbor_expand":
        base = _chunker_label(cfg.base)
        return f"neighbor_expand(window={cfg.window}, base={base})"
    if t == "fixed_overlap":
        return f"fixed_overlap(window_words={cfg.window_words}, step_words={cfg.step_words})"
    return t


def _build_target_config(tgt: BakeoffTarget, table: str) -> TargetConfig:
    """Render a bakeoff target (per-backend YAML row) into a runnable TargetConfig."""
    return TargetConfig(
        type=tgt.type,
        dsn_env=tgt.dsn_env,
        database=tgt.database_name,
        table=table,
        mode="overwrite",
        hnsw=False,
    )


def _build_cell_cfg(
    bakeoff: BakeoffConfig,
    tgt: BakeoffTarget,
    chunker_cfg,
    embedder_cfg,
    table: str,
) -> CellConfig:
    """Translate (bakeoff config, one target, one chunker, one embedder) into a runnable CellConfig."""
    rt = bakeoff.runtime or RuntimeConfig()
    return CellConfig(
        cell_name=(
            f"{bakeoff.name}__{tgt.type}__{chunker_key(chunker_cfg)}__"
            f"{embedder_key(embedder_cfg)}"
        ),
        source=bakeoff.source,
        framer=bakeoff.framer,
        chunker=chunker_cfg,
        embedder=embedder_cfg,
        extractor=NoneExtractor(),
        target=_build_target_config(tgt, table),
        runtime=rt,
    )


def _corpus_label(bakeoff: BakeoffConfig) -> str:
    """Best-effort human label for the corpus. `files.glob` -> the glob string; else source type."""
    src = bakeoff.source
    if getattr(src, "type", None) == "files":
        return getattr(src, "glob", "files")
    return getattr(src, "type", "unknown")


def run_bakeoff(cfg: BakeoffConfig) -> BakeoffResults:
    """Execute every (backend, chunker, embedder) cell, score against gold, return results.

    Caller must set `os.environ[t.dsn_env]` for every target before calling.
    Raises `RuntimeError` if any target's env var is unset.
    """
    for tgt in cfg.targets:
        if tgt.dsn_env not in os.environ:
            raise RuntimeError(
                f"DSN env var {tgt.dsn_env!r} for target type={tgt.type!r} is not set."
            )

    gold = load_gold_queries(cfg.gold_queries)
    matrix_combos = [(c, e) for c in cfg.matrix.chunkers for e in cfg.matrix.embedders]
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")

    # ----- Phase 1: ingest every (backend, chunker, embedder) cell -----
    ingest_meta: list[dict[str, Any]] = []
    for tgt in cfg.targets:
        for c, e in matrix_combos:
            table = combo_table(c, e)
            cell_cfg = _build_cell_cfg(cfg, tgt, c, e, table)
            t0 = time.time()
            res = run_cell(cell_cfg)
            wall = time.time() - t0
            if res.error:
                raise RuntimeError(
                    f"ingest failed for backend={tgt.type} combo={table}: {res.error}"
                )
            ingest_meta.append({
                "backend": tgt.type,
                "target": tgt,
                "chunker": c,
                "embedder": e,
                "table": table,
                "chunks": res.chunks_written,
                "wall_seconds": round(wall, 2),
                "embed_seconds": round(getattr(res, "embed_seconds", 0.0), 2),
            })

    # ----- Phase 2: embed gold queries once per unique embedder -----
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

    # ----- Phase 3: score every cell via its sink's query_top_k -----
    combo_results: list[ComboResult] = []
    for meta in ingest_meta:
        c = meta["chunker"]
        e = meta["embedder"]
        tgt = meta["target"]
        table = meta["table"]
        ck = chunker_key(c)
        ek = embedder_key(e)
        vecs = query_vecs_by_emb_key[ek]

        sink_cfg = _build_target_config(tgt, table)
        sink = load_sink(sink_cfg, embed_dim=e.dim)

        t_q = time.perf_counter()
        per_query: list[dict[str, Any]] = []
        per_query_scores: list[dict[str, float]] = []
        for i, g in enumerate(gold):
            top = sink.query_top_k(vecs[i], k=cfg.scoring.top_k)
            doc_ids = [t[0] for t in top]
            s = score_query(doc_ids, g.gold_doc_id, cfg.scoring.k)
            per_query_scores.append(s)
            per_query.append({
                "query": g.query,
                "gold_doc_id": g.gold_doc_id,
                "top_k": [
                    {"doc_id": t[0], "seq_num": t[1], "distance": t[2]}
                    for t in top
                ],
                **s,
            })
        query_wall = round(time.perf_counter() - t_q, 3)

        agg = aggregate_scores(per_query_scores)
        combo_results.append(ComboResult(
            backend=tgt.type,
            chunker_key=ck,
            embedder_key=ek,
            chunker_label=_chunker_label(c),
            embedder_label=e.model_name,
            table=table,
            ingest_chunks=meta["chunks"],
            ingest_wall_seconds=meta["wall_seconds"],
            ingest_embed_seconds=meta["embed_seconds"],
            query_wall_seconds=query_wall,
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
        gold_queries=[
            {"query": g.query, "gold_doc_id": g.gold_doc_id} for g in gold
        ],
        query_embed_seconds_by_embedder=query_embed_seconds_by_emb_key,
    )
