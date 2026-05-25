#!/usr/bin/env python3
"""Measure document-table fetch cost vs reconstructing a document from chunks.

This is intentionally small and local. It creates a temporary Postgres schema,
writes one large synthetic document through Chunkshop's PgSink, then compares:

1. `documents.lede_summary`/`lede_facts` lookup by doc_id
2. `documents.full_content` lookup by doc_id
3. `string_agg(chunks.original_content ORDER BY seq_num)` reconstruction

The point is not a universal benchmark. It proves the shape: once full document
text and lede artifacts live in a document row, doc-level context assembly stops
paying an O(chunks-per-doc) aggregation cost on every query.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import numpy as np
import psycopg

REPO = Path(__file__).resolve().parents[1]
PY_SRC = REPO / "python" / "src"
import sys

if str(PY_SRC) not in sys.path:
    sys.path.insert(0, str(PY_SRC))

from chunkshop.chunkers.base import Chunk  # noqa: E402
from chunkshop.config import TargetConfig  # noqa: E402
from chunkshop.sinks import load_sink  # noqa: E402


def timed(cur, sql: str, params: tuple, iterations: int) -> dict:
    durations = []
    size = 0
    for _ in range(iterations):
        t0 = time.perf_counter()
        cur.execute(sql, params)
        value = cur.fetchone()[0]
        durations.append((time.perf_counter() - t0) * 1000.0)
        size = len(value or "")
    ordered = sorted(durations)
    p95_idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "iterations": iterations,
        "bytes": size,
        "median_ms": statistics.median(durations),
        "p95_ms": ordered[p95_idx],
        "min_ms": min(durations),
        "max_ms": max(durations),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("CHUNKSHOP_TEST_DSN", "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"))
    parser.add_argument("--schema", default="chunkshop_doc_perf")
    parser.add_argument("--chunks", type=int, default=500)
    parser.add_argument("--chunk-chars", type=int, default=900)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    doc_id = "perf_doc"
    chunk_texts = [
        f"Section {i:04d}. " + ("This is repeated legal/news context for retrieval benchmarking. " * 20)
        for i in range(args.chunks)
    ]
    chunk_texts = [text[: args.chunk_chars] for text in chunk_texts]
    full_content = "\n\n".join(chunk_texts)

    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{args.schema}" CASCADE')
        conn.commit()

    cfg = TargetConfig(
        type="postgres",
        dsn=args.dsn,
        database=args.schema,
        table="chunks",
        mode="overwrite",
        hnsw=False,
        documents={
            "enabled": True,
            "table": "documents",
            "store_full_content": True,
            "store_lede_report": True,
            "fts": {"enabled": True, "language": "english"},
        },
    )
    sink = load_sink(cfg, embed_dim=4)
    sink.create_table()
    sink.write_document_record(
        doc_id=doc_id,
        title="Synthetic performance document",
        content=full_content,
        chunk_count=args.chunks,
        metadata={
            "lede_report": {
                "summary": "Synthetic document summary for context packing.",
                "key_facts": ["Synthetic fact one", "Synthetic fact two"],
                "search_text": "synthetic performance document summary facts",
            }
        },
    )
    chunks = [
        Chunk(doc_id=doc_id, seq_num=i, original_content=text, embedded_content=text, metadata={})
        for i, text in enumerate(chunk_texts)
    ]
    embeddings = np.tile(np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32), (len(chunks), 1))
    sink.write_document(doc_id, chunks, embeddings, [[] for _ in chunks])

    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        # Warm cache and query plans.
        cur.execute(f'SELECT full_content FROM "{args.schema}"."documents" WHERE doc_id = %s', (doc_id,))
        cur.fetchone()
        cur.execute(
            f'SELECT string_agg(original_content, E\'\\n\\n\' ORDER BY seq_num) '
            f'FROM "{args.schema}"."chunks" WHERE doc_id = %s',
            (doc_id,),
        )
        cur.fetchone()

        doc_lookup = timed(
            cur,
            f'SELECT full_content FROM "{args.schema}"."documents" WHERE doc_id = %s',
            (doc_id,),
            args.iterations,
        )
        chunk_reconstruct = timed(
            cur,
            f'SELECT string_agg(original_content, E\'\\n\\n\' ORDER BY seq_num) '
            f'FROM "{args.schema}"."chunks" WHERE doc_id = %s',
            (doc_id,),
            args.iterations,
        )
        summary_lookup = timed(
            cur,
            f'SELECT lede_summary || E\'\\n\' || lede_facts::text '
            f'FROM "{args.schema}"."documents" WHERE doc_id = %s',
            (doc_id,),
            args.iterations,
        )
        cur.execute(f'SELECT lede_summary, lede_facts FROM "{args.schema}"."documents" WHERE doc_id = %s', (doc_id,))
        summary, facts = cur.fetchone()

    speedup = (
        chunk_reconstruct["median_ms"] / doc_lookup["median_ms"]
        if doc_lookup["median_ms"]
        else None
    )
    result = {
        "schema": args.schema,
        "doc_id": doc_id,
        "chunks": args.chunks,
        "chunk_chars": args.chunk_chars,
        "full_content_chars": len(full_content),
        "doc_lookup": doc_lookup,
        "summary_lookup": summary_lookup,
        "chunk_reconstruct": chunk_reconstruct,
        "median_speedup_doc_lookup_vs_chunk_reconstruct": speedup,
        "median_speedup_summary_lookup_vs_chunk_reconstruct": (
            chunk_reconstruct["median_ms"] / summary_lookup["median_ms"]
            if summary_lookup["median_ms"]
            else None
        ),
        "summary_preserved": summary == "Synthetic document summary for context packing.",
        "facts_preserved": facts == ["Synthetic fact one", "Synthetic fact two"],
    }
    print(json.dumps(result, indent=2, sort_keys=True))

    if not args.keep:
        with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{args.schema}" CASCADE')
            conn.commit()


if __name__ == "__main__":
    main()
