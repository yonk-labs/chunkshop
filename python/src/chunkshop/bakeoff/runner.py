"""`run_bakeoff(cfg: BakeoffConfig) -> BakeoffResults` (multi-backend, embed-once).

Outer loop over `matrix.chunkers × matrix.embedders`. For each (chunker, embedder)
combo, the source is read once, the chunker runs once, the embedder runs once,
and every chunk is fanned out to every backend in `cfg.targets` via per-sink
`write_document` calls. This eliminates the 3-4× duplicated embed work that
the prior outer-target-loop design caused, and makes the per-backend
`ingest_wall_seconds` reflect only write cost (not shared embed cost).

Then gold queries are embedded once per embedder, and for every (backend, combo)
cell the sink's native `query_top_k` is called for each gold query and scored.

The runner does not own connection cleanup — the CLI (or the user) is
responsible for dropping schemas / databases / files after the run.
"""
from __future__ import annotations

import os
import time
from dataclasses import replace as _replace
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
from chunkshop.chunkers import load_chunker
from chunkshop.config import (
    NoneExtractor,
    RuntimeConfig,
    TargetConfig,
)
from chunkshop.embedders import load_embedder
from chunkshop.extractors import load_extractor
from chunkshop.framers import load_framer
from chunkshop.sinks import load_sink
from chunkshop.sources import load_source


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
    # ClickHouse-only optional `engine` field; other backends ignore it.
    extra = {}
    if getattr(tgt, "engine", None) is not None:
        extra["engine"] = tgt.engine
    return TargetConfig(
        type=tgt.type,
        dsn_env=tgt.dsn_env,
        database=tgt.database_name,
        table=table,
        mode="overwrite",
        hnsw=False,
        **extra,
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

    # OMP/MKL/BLAS thread cap — must run before numpy/ONNX import path.
    # Mirrors runner.run_cell so the bakeoff matches single-cell behavior.
    rt = cfg.runtime or RuntimeConfig()
    n_threads = str(rt.omp_num_threads)
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, n_threads)

    # ----- Phase 1: per-(chunker, embedder), embed once and fan-out write to all backends -----
    # Each combo is one pass through the source. Per-backend wall_seconds tracks
    # ONLY the sink.write_document time, isolated from the shared embed cost.
    ingest_meta: list[dict[str, Any]] = []
    for c, e in matrix_combos:
        table = combo_table(c, e)
        ck = chunker_key(c)
        ek = embedder_key(e)

        # Build one sink per backend; create_table on each.
        sinks_for_combo: list[tuple[BakeoffTarget, Any]] = []  # (target, sink)
        for tgt in cfg.targets:
            sink_cfg = _build_target_config(tgt, table)
            sink = load_sink(sink_cfg, embed_dim=e.dim)
            sink.create_table()
            sinks_for_combo.append((tgt, sink))

        # Build the pipeline pieces once for this combo.
        source = load_source(cfg.source)
        framer = load_framer(cfg.framer)
        embedder = load_embedder(e)
        # SemanticChunker(boundary_model="same") reuses the main embedder's model.
        shared_boundary_model = getattr(embedder, "_model", None)
        chunker = load_chunker(
            c, main_embedder=e, shared_boundary_model=shared_boundary_model,
        )
        extractor = load_extractor(NoneExtractor())

        # Per-backend accumulators
        write_wall_by_backend: dict[str, float] = {tgt.type: 0.0 for tgt, _ in sinks_for_combo}
        chunks_written_by_backend: dict[str, int] = {tgt.type: 0 for tgt, _ in sinks_for_combo}
        total_embed_seconds = 0.0
        t_combo_start = time.time()

        for raw in source.iter_documents():
            for doc in framer.frame(raw):
                chunks = chunker.chunk(doc)
                if not chunks:
                    continue
                texts = [ch.embedded_content for ch in chunks]
                t_e = time.perf_counter()
                embeddings = embedder.embed(texts)
                total_embed_seconds += time.perf_counter() - t_e
                results = [extractor.extract(ch.original_content) for ch in chunks]
                tags = [r.tags for r in results]
                # Layered metadata merge — same chunker-wins semantics as runner.run_cell.
                doc_meta = doc.metadata or {}
                chunks = [
                    _replace(ch, metadata={**doc_meta, **r.metadata, **ch.metadata})
                    for ch, r in zip(chunks, results)
                ]
                # Fan-out write to every backend.
                for tgt, sink in sinks_for_combo:
                    t_w = time.perf_counter()
                    sink.write_document(doc.id, chunks, embeddings, tags)
                    write_wall_by_backend[tgt.type] += time.perf_counter() - t_w
                    chunks_written_by_backend[tgt.type] += len(chunks)

        # One ingest_meta entry per backend, all sharing the same total_embed_seconds.
        # wall_seconds is per-backend write-only time.
        for tgt, _sink in sinks_for_combo:
            ingest_meta.append({
                "backend": tgt.type,
                "target": tgt,
                "chunker": c,
                "embedder": e,
                "table": table,
                "chunks": chunks_written_by_backend[tgt.type],
                "wall_seconds": round(write_wall_by_backend[tgt.type], 2),
                "embed_seconds": round(total_embed_seconds, 2),
            })
        # Free per-combo resources before the next combo loads its embedder.
        del embedder, chunker, framer, source

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
