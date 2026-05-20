# Memory scheduling — pick your poison

chunkshop's agent-memory layer is two batch jobs. **You bring the
scheduler.** This directory shows four common ways to wire them.

| Pattern | When | Subdir |
|---|---|---|
| **cron + systemd timer** | Linux box, no orchestrator | [`cron/`](cron/) |
| **k8s CronJob** | Already on Kubernetes | [`k8s-cronjob/`](k8s-cronjob/) |
| **In-process (Python)** | chunkshop runs *inside* your agent server | [`in-process-python/`](in-process-python/) |
| **In-process (Rust)** | Same, for `chunkshop-rs` consumers | [`in-process-rust/`](in-process-rust/) |

All four drive the same two YAMLs:

- **`memory/realtime.yaml`** — runs every minute. Writes
  `tier='provisional'` rows so a fresh agent reply has memory to read
  within ~1 minute.
- **`memory/consolidate.yaml`** — runs nightly (or whatever cadence).
  Segments quiet sessions into episodes, extracts SPO facts via a
  consolidator you wire up, supersedes the provisional rows with
  `tier='consolidated'`.

Python presets live at [`python/src/chunkshop/configs/memory/*.yaml`](../../../python/src/chunkshop/configs/memory/);
Rust presets at [`rust/chunkshop/configs/memory/*.yaml`](../../../rust/chunkshop/configs/memory/).
They're portable across languages — same shape, same defaults — except
the `consolidator:` block in `consolidate.yaml` (Python uses a dynamic
`module:`/`function:` callable, Rust names a built-in `mode:`).

## How to pick

- **Simplest deployment, one box:** cron + systemd timer.
- **Fleet / multi-tenant / want concurrency policies:** k8s CronJob.
- **chunkshop is a library inside your agent server, you don't want a
  second process / pod:** in-process scheduler. Same trade-off both
  languages: easier ops, but a crash in your agent now also stops your
  memory consolidation, so make sure you handle restarts cleanly.

## Required environment

Every pattern reads `CHUNKSHOP_MEMORY_DSN` for the staging table and
the `agent_memory.memory` target. Make sure:

```bash
export CHUNKSHOP_MEMORY_DSN="postgresql://app:secret@db.internal:5432/agent_memory"
```

…is set at the right scope — environment file for systemd, Secret for
k8s, env for in-process. The Postgres database itself needs `pgvector`
loaded:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

chunkshop's first run will create `chunkshop_staging` and
`agent_memory.memory` for you (idempotent DDL).
