"""In-process memory scheduling for a Python agent runtime.

Run with:
    export CHUNKSHOP_MEMORY_DSN=postgresql://app:secret@localhost:5432/agent_memory
    python run.py

This drives the two memory cells from the same asyncio loop your agent
server runs on. See README.md for FastAPI integration."""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path

from chunkshop.config import load_config
from chunkshop.memory import ensure_staging_table, stage_event
from chunkshop.runner import run_cell

log = logging.getLogger("chunkshop.memory.scheduler")

# Adjust these paths to wherever your deployed presets live. The
# defaults work when running this script from inside the chunkshop repo.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
REALTIME_CFG = REPO_ROOT / "python/src/chunkshop/configs/memory/realtime.yaml"
CONSOLIDATE_CFG = REPO_ROOT / "python/src/chunkshop/configs/memory/consolidate.yaml"


def stage_turn(session_id: str, role: str, content: str, seq: int) -> None:
    """Call this from your agent's message handler.

    Idempotent on `event_id` (sha1 of session_id/seq/content), so
    re-staging the same turn is a no-op. Synchronous and fast (<5ms
    in practice) — safe to call inline from a request handler."""
    dsn = os.environ["CHUNKSHOP_MEMORY_DSN"]
    stage_event(dsn, session_id=session_id, role=role, content=content,
                seq=seq, table="chunkshop_staging")


async def _run_cell_periodically(name: str, cfg_path: Path, interval_s: int) -> None:
    """Run one memory cell on a fixed interval. Survives transient
    errors — logs them and keeps the loop alive."""
    while True:
        try:
            cfg = load_config(str(cfg_path))
            # run_cell is sync — embedder + DB I/O block — so we run
            # it in a thread to keep the asyncio loop responsive.
            r = await asyncio.to_thread(run_cell, cfg)
            if r.error:
                log.error("[memory:%s] error: %s", name, r.error)
            else:
                log.info("[memory:%s] docs=%d chunks=%d wall=%.2fs",
                         name, r.docs_processed, r.chunks_written, r.wall_seconds)
        except Exception:
            log.exception("[memory:%s] tick crashed; loop continues", name)
        await asyncio.sleep(interval_s)


async def memory_scheduler_loop(
    realtime_interval_s: int = 60,
    consolidate_interval_s: int = 3600,
) -> None:
    """Run forever — call once from your app startup. Returns only on
    cancellation. Bootstrap-DDL is idempotent and runs every start."""
    dsn = os.environ["CHUNKSHOP_MEMORY_DSN"]
    # The DDL is idempotent — safe to call on every boot.
    ensure_staging_table(dsn, table="chunkshop_staging")

    await asyncio.gather(
        _run_cell_periodically("realtime",    REALTIME_CFG,    realtime_interval_s),
        _run_cell_periodically("consolidate", CONSOLIDATE_CFG, consolidate_interval_s),
    )


# --- demo / smoke test ------------------------------------------------------

async def _demo() -> None:
    """Demo: stage a 5-turn session, run realtime once, run consolidate
    once. Used to verify the wiring works end-to-end against a real PG."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dsn = os.environ["CHUNKSHOP_MEMORY_DSN"]
    ensure_staging_table(dsn, table="chunkshop_staging")

    for seq, (role, content) in enumerate([
        ("user",      "We use Redis for the job queue."),
        ("assistant", "Understood — Redis backs the queue."),
        ("user",      "Actually we migrated to Postgres last week."),
        ("assistant", "Noted. Switching mental model to Postgres LISTEN/NOTIFY."),
        ("user",      "And we use pg_partman for the audit table partitions."),
    ], start=1):
        stage_turn("demo-session-1", role, content, seq)

    log.info("staged 5 turns; running realtime...")
    rt = await asyncio.to_thread(run_cell, load_config(str(REALTIME_CFG)))
    log.info("realtime: error=%s chunks=%d", rt.error, rt.chunks_written)

    # Backdate so consolidate's min_age_seconds gate passes for the demo.
    import psycopg
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("UPDATE public.chunkshop_staging "
                    "SET staged_at = now() - interval '2 hours'")
        c.commit()
    cfg = load_config(str(CONSOLIDATE_CFG))
    cfg.source.min_age_seconds = 0
    log.info("running consolidate (min_age=0 for demo)...")
    cs = await asyncio.to_thread(run_cell, cfg)
    log.info("consolidate: error=%s chunks=%d", cs.error, cs.chunks_written)

    # Show the result.
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT tier, kind, count(*) FROM agent_memory.memory "
                    "GROUP BY tier, kind ORDER BY tier, kind")
        for row in cur.fetchall():
            log.info("agent_memory.memory: tier=%s kind=%s count=%d", *row)


if __name__ == "__main__":
    asyncio.run(_demo())
