"""End-to-end example: chunkshop SP-A memory → pg-raggraph ingest_records.

Reads $CHUNKSHOP_MEMORY_DSN; that's the only required env var. chunkshop
has no pg-raggraph dep — the pg-raggraph call below runs only under
--ingest, and only if pg-raggraph is importable in the consumer env.
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

from chunkshop.memory import (
    ensure_staging_table,
    stage_events,
    read_pre_chunked,
)


SAMPLE_SESSION = [
    {"session_id": "demo-s1", "seq": 1, "role": "user",
     "content": "We use Redis for the job queue."},
    {"session_id": "demo-s1", "seq": 2, "role": "assistant",
     "content": "Understood, Redis backs the queue."},
    {"session_id": "demo-s2", "seq": 1, "role": "user",
     "content": "We migrated the queue from Redis to Postgres."},
    {"session_id": "demo-s2", "seq": 2, "role": "assistant",
     "content": "Confirmed, Postgres LISTEN/NOTIFY now backs the queue."},
]


def _dsn() -> str:
    dsn = os.environ.get("CHUNKSHOP_MEMORY_DSN")
    if not dsn:
        sys.exit("set CHUNKSHOP_MEMORY_DSN to your Postgres DSN")
    return dsn


def seed(dsn: str) -> None:
    ensure_staging_table(dsn, table="chunkshop_staging", schema="public")
    stage_events(dsn, SAMPLE_SESSION,
                 table="chunkshop_staging", schema="public")
    print(f"staged {len(SAMPLE_SESSION)} events")


def consolidate(dsn: str) -> None:
    """Drive the consolidate cell programmatically (or run via CLI:
    `chunkshop ingest --config src/chunkshop/configs/memory/consolidate.yaml`)."""
    from chunkshop.config import load_config
    from chunkshop.runner import run_cell

    preset = (Path(__file__).resolve().parents[2]
              / "src/chunkshop/configs/memory/consolidate.yaml")
    if not preset.exists():
        # fallback for installed package: locate via chunkshop.__file__
        import chunkshop
        preset = (Path(chunkshop.__file__).resolve().parent
                  / "configs/memory/consolidate.yaml")
    cfg = load_config(str(preset))
    cfg.source.min_age_seconds = 0      # demo: don't wait an hour
    r = run_cell(cfg)
    print(f"consolidate: error={r.error} chunks_written={r.chunks_written}")


def ingest(dsn: str, namespace: str = "demo") -> None:
    try:
        from pg_raggraph import GraphRAG  # type: ignore
    except ImportError:
        sys.exit("install pg-raggraph[chunkshop]>=0.4.3 to run --ingest")

    records = list(read_pre_chunked(dsn, namespace=None))   # all sessions
    print(f"read {len(records)} session records from agent_memory.memory")
    if not records:
        return
    # Configure GraphRAG against the SAME Postgres (or a different one if
    # you keep memory and graph stores separate).
    import asyncio

    async def _go():
        rag = GraphRAG(dsn=dsn)
        await rag.ensure_schema()
        stats = await rag.ingest_records(records, namespace=namespace)
        print("pg-raggraph ingest stats:", stats)

    asyncio.run(_go())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", action="store_true",
                   help="stage the sample session into chunkshop_staging")
    p.add_argument("--consolidate", action="store_true",
                   help="run chunkshop consolidate.yaml against staged rows")
    p.add_argument("--ingest", action="store_true",
                   help="read agent_memory.memory and feed pg-raggraph")
    p.add_argument("--namespace", default="demo")
    args = p.parse_args()

    dsn = _dsn()
    if args.seed:
        seed(dsn)
    if args.consolidate:
        consolidate(dsn)
    if args.ingest:
        ingest(dsn, namespace=args.namespace)
    if not (args.seed or args.consolidate or args.ingest):
        p.print_help()


if __name__ == "__main__":
    main()
