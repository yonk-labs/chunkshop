# Incremental ingest, deltas, and CDC into chunkshop

> **Status:** ships in chunkshop v0.2.x. The `delete_orphans` flag is new in
> this release. The patterns below have all been used in production setups.

## TL;DR

chunkshop is a **batch worker** with idempotent upserts. There is no native
streaming runtime, no CDC tap, no change-data subscription. The pipeline is
designed so you can run it as the worker behind a delta loop:

- The target table is keyed `(doc_id, seq_num)`.
- The sink uses `INSERT ... ON CONFLICT (id) DO UPDATE`.
- Re-running a cell on the same docs is effectively a no-op; on changed docs
  it replaces those chunks atomically per-document; on new docs it adds them.
- The `source_tag` column is **write-once on conflict** — multi-source
  provenance survives concurrent writers.
- With `target.delete_orphans: true`, chunks for a doc that **shrunk** are
  deleted within the same write transaction.

You bring the scheduler and the change-detector. chunkshop is the idempotent
worker on the receiving end. This doc covers the five common hookup patterns,
the deletion gap, and the third-party tools people pair with chunkshop for
each piece.

---

## The fact that makes deltas work: idempotent upserts

Every row is keyed by primary key `id = "{doc_id}::{seq_num}"`, with a unique
index on `(doc_id, seq_num)`. The sink writes per-document, one short-lived
transaction per `write_document`. The conflict clause looks like:

```sql
INSERT INTO chunks (id, doc_id, seq_num, original_content, embedded_content,
                    tags, metadata, embedding, source, ...)
VALUES (...)
ON CONFLICT (id) DO UPDATE SET
  original_content = EXCLUDED.original_content,
  embedded_content = EXCLUDED.embedded_content,
  tags             = EXCLUDED.tags,
  metadata         = EXCLUDED.metadata,
  embedding        = EXCLUDED.embedding,
  -- promoted columns updated; `source` deliberately NOT in the SET clause
  ...
;
```

Three replay scenarios:

| Scenario                  | Without `delete_orphans` | With `delete_orphans: true` |
|---------------------------|--------------------------|------------------------------|
| Doc unchanged             | Rows rewritten with same content (no-op-in-effect) | Same |
| Doc edited, same chunk count | Content/embedding/metadata updated atomically per-row | Same |
| Doc edited, **fewer** chunks | Old chunks at higher seq_num **remain as orphans** | Old chunks at `seq_num >= new_count` are deleted in the same transaction |
| Doc edited, **more** chunks | New seq_nums inserted, existing ones updated | Same (DELETE is `seq_num >= new_count`, catches nothing) |
| New doc                   | Inserted | Same |
| Doc deleted from source   | **Chunks remain** — see "the deletion gap" below | Same — `delete_orphans` only fires per-write |

**`source` is write-once.** Two cells writing the same `(doc_id, seq_num)` —
the first writer's `source_tag` wins forever. This protects multi-source
filtering: if `cell_a` writes `d1::0` with `source_tag='cell_a'`, and `cell_b`
later upserts the same row, `cell_b`'s embedding/content lands but the row
still says `source = 'cell_a'`. This is provenance, not a race.

**`PgVectorSink.write_document` opens a fresh connection and commits
per-document.** This makes `SELECT COUNT(DISTINCT doc_id) FROM ...` from
another psql session a valid live-progress query. A mid-run crash only loses
the in-flight document — rerun upserts the rest cleanly.

---

## Pattern A — Cron + `pg_table` source with a `WHERE` clause (the 80% case)

You already have a Postgres table called `sales_notes(id, body, updated_at, ...)`.
Wire chunkshop's `pg_table` source straight at it with a sliding window:

```yaml
# docs/samples/incremental-pg-table/sample.yaml
cell_name: sales_notes_incremental

source:
  type: pg_table
  dsn_env: SALES_DB_DSN
  schema: public
  table: sales_notes
  id_column: id
  content_column: body
  title_column: subject
  where: "updated_at > NOW() - interval '30 minutes'"

framer: { type: identity }

chunker:
  type: hierarchy
  max_chars: 1200

embedder:
  type: fastembed
  model_name: "Xenova/bge-small-en-v1.5-int8"
  dim: 384
  threads: 2
  batch_size: 64

target:
  dsn_env: VECTORS_DB_DSN
  schema: rag
  table: notes_chunks
  mode: create_if_missing  # bootstrap the target on first run
  source_tag: sales_notes
  delete_orphans: true     # close the per-doc shrink gap
  hnsw: true
```

**Run on cron every 5–15 minutes.** The window must overlap the cron interval
to absorb clock drift:

```cron
*/15 * * * * cd /opt/chunkshop && /usr/local/bin/chunkshop ingest \
  --config /etc/chunkshop/sales_notes_incremental.yaml >> /var/log/chunkshop/sales_notes.log 2>&1
```

Or in Kubernetes:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata: { name: chunkshop-sales-notes }
spec:
  schedule: "*/15 * * * *"
  concurrencyPolicy: Forbid     # avoid overlap if a run goes long
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: chunkshop
            image: ghcr.io/yonk-labs/chunkshop:0.2
            command: ["chunkshop", "ingest", "--config", "/cfg/sales_notes_incremental.yaml"]
            envFrom:
              - secretRef: { name: chunkshop-dsns }
            volumeMounts:
              - { name: cfg, mountPath: /cfg }
          volumes:
            - name: cfg
              configMap: { name: sales-notes-config }
          restartPolicy: OnFailure
```

**Trade-offs.**
- ✅ Simple. Zero state. Works on day one.
- ✅ Idempotent — re-running over the same window costs the upsert work but doesn't corrupt.
- ⚠️ Sliding window has gaps if a cron run goes longer than the window. Use `concurrencyPolicy: Forbid` and make the window > 2× the longest-expected run time.
- ⚠️ Doesn't capture deletes. Pattern D handles that.

---

## Pattern B — Watermarked `WHERE` (durable cursor)

Keep a single-row state table the wrapper script reads + updates around each run:

```sql
CREATE TABLE chunkshop.cursor (
  source_tag   text PRIMARY KEY,
  last_seen_at timestamptz NOT NULL,
  updated_at   timestamptz NOT NULL DEFAULT now()
);
```

A thin Python wrapper renders the YAML's `where` clause from the cursor,
runs chunkshop, then bumps the cursor. See
[`scripts/run_incremental_watermark.py`](../scripts/run_incremental_watermark.py).

```bash
python3 scripts/run_incremental_watermark.py \
  --source-tag sales_notes \
  --source-dsn-env SALES_DB_DSN \
  --target-dsn-env VECTORS_DB_DSN \
  --source-schema public --source-table sales_notes \
  --updated-column updated_at \
  --config docs/samples/incremental-pg-table/sample.yaml \
  --cursor-schema chunkshop --cursor-table cursor
```

The wrapper uses `pyyaml` (already a chunkshop dep) so multi-line block
scalars and quoted strings in the source YAML round-trip cleanly. End-to-end
demo: [`docs/samples/incremental-pg-table/run_demo.sh`](samples/incremental-pg-table/run_demo.sh) — sets up a fake source table,
runs the wrapper twice, inserts a new row, runs again, prints the cursor
state. Verified to produce the expected windowed-delta behavior on each
re-run.

The wrapper:

1. Reads `last_seen_at` from `chunkshop.cursor`.
2. Reads `MAX(updated_at)` from the source table — this is the new high-water mark.
3. Templates the YAML's `where` clause to `updated_at > '$last_seen_at' AND updated_at <= '$new_high'`.
4. Runs `chunkshop ingest`.
5. On success, `UPDATE chunkshop.cursor SET last_seen_at = '$new_high'`.

**Trade-offs.**
- ✅ Exactly-once-ish. No window-overlap math. Resilient to clock skew.
- ✅ Re-running after a crash resumes from the last successful watermark.
- ⚠️ Single-writer per `source_tag` — don't run two of these in parallel against the same cursor row.
- ⚠️ The cursor table is now part of your operational story (backup, monitoring).

---

## Pattern C — Staging-file inbox (proxy / queue / webhook)

Your data comes through a proxy, webhook, or message queue. **Don't try to
make chunkshop a long-running consumer.** Use the filesystem as a durable
buffer:

```
/var/inbox/notes/                # written by the proxy/queue worker
  YYYY/MM/DD/<uuid>.md
/var/inbox-batches/              # cron rotates files into batches
  batch-2026-04-29-1430/
    note-001.md
    note-002.md
processed/                       # successful batches archived here
```

Workflow:

1. **Proxy/queue consumer** writes each new note as a markdown file into
   `/var/inbox/notes/...`. Frontmatter for metadata, body for content.
2. **Cron task** (every 5 min): `mkdir batch-$(date +%F-%H%M)`, `mv` the
   files in, run chunkshop, on success move the batch dir to `processed/`.

```yaml
# /etc/chunkshop/notes_batch.yaml
cell_name: notes_batch
source:
  type: files
  glob: /var/inbox-batches/CURRENT/**/*.md
  parse_frontmatter: true
chunker: { type: hierarchy }
embedder: { type: fastembed, model_name: "Xenova/bge-small-en-v1.5", quantization: int8 }
target:
  dsn_env: VECTORS_DB_DSN
  schema: rag
  table: notes_chunks
  mode: append
  source_tag: notes_inbox
  delete_orphans: true
```

Wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail
BATCH_DIR="/var/inbox-batches/batch-$(date +%F-%H%M)"
mkdir -p "$BATCH_DIR"
mv /var/inbox/notes/* "$BATCH_DIR/" 2>/dev/null || { rmdir "$BATCH_DIR"; exit 0; }
ln -sfn "$BATCH_DIR" /var/inbox-batches/CURRENT
chunkshop ingest --config /etc/chunkshop/notes_batch.yaml
mv "$BATCH_DIR" /var/inbox-batches/processed/
```

**Trade-offs.**
- ✅ Filesystem is the queue. Easy to debug — you can `ls` the inbox.
- ✅ Decouples ingest cadence from producer cadence — the proxy can fire and forget.
- ⚠️ Single-host unless you mount NFS or use object storage (then see Pattern E).
- ⚠️ You own retention — `processed/` grows forever unless you prune it.

---

## Pattern D — Postgres CDC → staging table → chunkshop (real-time-ish)

When you need millisecond-latency on `INSERT`/`UPDATE` from a source Postgres,
chunkshop is **not** the streaming engine. Use logical replication or a CDC
tool to land changes in a staging table, then point chunkshop at it with an
`applied = false` filter.

Architecture:

```
source.sales_notes (OLTP)
       │
       │  Debezium / pgoutput / Estuary Flow / AWS DMS
       ▼
chunkshop_staging.sales_notes_changes (id, body, updated_at, op, applied)
       │
       │  cron (1 min) — chunkshop ingest with WHERE applied=false
       ▼
rag.notes_chunks (vector store)
```

Staging table:

```sql
CREATE TABLE chunkshop_staging.sales_notes_changes (
  id           bigint PRIMARY KEY,
  body         text   NOT NULL,
  updated_at   timestamptz NOT NULL,
  op           char(1) NOT NULL,         -- 'I' insert, 'U' update, 'D' delete
  applied      boolean NOT NULL DEFAULT false,
  applied_at   timestamptz
);
```

chunkshop config:

```yaml
source:
  type: pg_table
  dsn_env: STAGING_DB_DSN
  table: sales_notes_changes
  id_column: id
  body_column: body
  where: "applied = false AND op IN ('I', 'U')"   # deletes handled separately
target:
  mode: append
  source_tag: sales_notes_cdc
  delete_orphans: true
```

After each run, the wrapper marks rows applied:

```sql
UPDATE chunkshop_staging.sales_notes_changes
SET applied = true, applied_at = now()
WHERE applied = false AND updated_at <= '$watermark';
```

For `op = 'D'` (deletes), a sibling cleanup script handles them — chunkshop
doesn't delete docs from the target table on its own.

**Trade-offs.**
- ✅ Latency floor is your CDC lag (typically seconds).
- ✅ Captures inserts, updates, and (with the cleanup script) deletes.
- ⚠️ Debezium/CDC infrastructure is its own operational concern.
- ⚠️ The staging table needs retention — prune `applied = true` rows older than N days.

---

## Pattern E — Object-storage events → batch → chunkshop

Your producer writes JSON or markdown to S3 / R2 / GCS. Use bucket events to
trigger ingest of new objects:

```
S3 bucket: notes-raw/
  └── YYYY/MM/DD/<uuid>.json

   ┌───── S3 Event Notification ─────┐
   │  on PUT → SQS queue              │
   │  cron pops batch from SQS        │
   │  chunkshop ingest with files src │
   └──────────────────────────────────┘
```

The `s3` source in chunkshop already does the read side. The simplest
non-CDC version: skip the SQS step and use a dated prefix:

```yaml
source:
  type: s3
  bucket: notes-raw
  prefix: "${YEAR}/${MONTH}/${DAY}/${HOUR}/"
  endpoint_url: ${S3_ENDPOINT_URL}     # for minio / R2 / Cloudflare
target:
  mode: append
  source_tag: notes_s3
  delete_orphans: true
```

Render the prefix to the previous hour at cron time, and the upserts dedupe
against any reruns.

**Trade-offs.**
- ✅ Producer and consumer are fully decoupled.
- ✅ Object-store retention is solved (lifecycle rules).
- ⚠️ Latency = cron interval, plus S3 list-prefix is eventually consistent.

---

## Pattern F — Inline (library) mode: your app **is** the source

You don't want a YAML-defined source at all. Your service already knows
when content arrives — webhook, queue, in-process generator, slack bot,
admin form save. You just want chunkshop to be the chunk-embed-store loop
behind a function call.

```yaml
source:
  type: inline   # mandatory; no other fields. Pipeline rejects any other source type.

chunker: { type: hierarchy, max_chars: 1200 }
embedder: { type: fastembed, model_name: "Xenova/bge-small-en-v1.5-int8", dim: 384 }
target:
  schema: rag
  table: notes_chunks
  mode: append
  source_tag: notes_app
  delete_orphans: true
```

### Python

```python
import chunkshop

shop = chunkshop.Pipeline.from_yaml("config.yaml")

# On webhook / queue message:
shop.ingest_text(doc_id="note-001", text="...", metadata={"author": "alice"})

# On record delete:
shop.delete_document("note-001")
```

### Rust

```rust
let mut shop = chunkshop::Pipeline::from_yaml("config.yaml").await?;

// On webhook / queue message:
shop.ingest_text("note-001", "...", serde_json::json!({"author": "alice"})).await?;

// On record delete:
shop.delete_document("note-001").await?;
```

The same YAML drives both languages. The same `(doc_id, seq_num)` rows
land in the same target table. Vectors are interchangeable —
chunkshop's cross-language wire-format claim, applied at the per-call
level instead of per-cell.

`delete_document` is **scoped to the pipeline's `source_tag`**: a Pipeline
configured for `source_tag = "X"` cannot delete rows owned by source_tag
"Y". Same write-once provenance contract the upsert path enforces.

**Trade-offs.**
- ✅ The most ergonomic option when your app is already the source of truth.
- ✅ No cron, no watermark, no staging table — just function calls.
- ✅ Combines `delete_orphans` (per-doc shrink) with `delete_document` (full doc removal) for full insert/update/delete semantics.
- ⚠️ One in-process embedder per process — no shared model cache across services. If you have ten services chunking, that's ten copies of the model in RAM.
- ⚠️ Embedding latency is now in your request path. For high-RPS endpoints, push to a queue and let a worker call `ingest_text` instead.
- ⚠️ Crash safety: a process crash mid-`ingest_text` loses that doc. Pair with idempotent retries in your queue/webhook layer.

End-to-end runnable demos for both languages live in
[`docs/samples/inline-mode/`](samples/inline-mode/) — same YAML, same
target table, same per-step behavior.

---

## The deletion gap — what `delete_orphans` does and does not cover

There are **two** delete cases. The flag handles one:

### ✅ Per-doc shrink (covered by `delete_orphans: true`)

Doc had 12 chunks last run, has 8 this run. With the flag on, after the
upsert the sink runs:

```sql
DELETE FROM rag.notes_chunks
WHERE doc_id = $1 AND seq_num >= $new_chunk_count;
```

inside the same transaction. Either the new chunkset lands and the orphans
are gone, or neither — there is no half-written intermediate state.

### ⚠️ Doc deleted from source (still a gap)

Doc was in last run, isn't in this run. The flag does not help: chunkshop
can't delete a doc it doesn't see. Two real-world fixes:

**Fix 1: snapshot mode (full-corpus sources only).** If your source is a full
file glob or a full table scan, you can compute the set of `doc_id`s seen
this run vs. the set in the target table:

```sql
DELETE FROM rag.notes_chunks
WHERE source = 'sales_notes'
  AND doc_id NOT IN (SELECT id::text FROM source.sales_notes);
```

Run this as a sibling cleanup, scoped to your `source_tag`. **Do not run it
when your source is a `WHERE`-windowed view of the table — you'd delete every
doc outside the window.**

**Fix 2: delete-events from CDC.** In Pattern D, `op = 'D'` rows in the staging
table tell you exactly what to delete. A separate small script consumes them:

```sql
DELETE FROM rag.notes_chunks
WHERE source = 'sales_notes_cdc'
  AND doc_id IN (
    SELECT id::text FROM chunkshop_staging.sales_notes_changes
    WHERE op = 'D' AND applied = false
  );
UPDATE chunkshop_staging.sales_notes_changes
SET applied = true WHERE op = 'D' AND applied = false;
```

A first-class `purge_missing_docs` flag may land in a future release. Today,
the pattern is: chunkshop handles writes; you handle deletes via a sibling
script that runs alongside the cron.

---

## Third-party tools that handle the moving parts

chunkshop is the worker. Here's what people pair it with for each role.

### Schedulers — when, how often, retry semantics

| Tool                | When                                        | Notes                                                                 |
|---------------------|---------------------------------------------|-----------------------------------------------------------------------|
| **cron / systemd timers** | Single host, simple                  | Zero ops. Pair with `flock` or `concurrencyPolicy: Forbid` to avoid overlap. |
| **k8s `CronJob`**   | Already on k8s                              | `concurrencyPolicy: Forbid` is the only knob you usually need.        |
| **GitHub Actions `schedule`** | Tiny corpora, public repos        | 5-min minimum cadence; runner cold-start is multi-second.             |
| **Apache Airflow**  | Existing Airflow shop                       | Heavyweight. Overkill if chunkshop is your only DAG.                  |
| **Prefect**         | Modern Python-first scheduling              | `prefect.deploy()` makes "every 15 min" trivial; Cloud option exists. |
| **Dagster**         | Asset-oriented (chunkshop is one asset of many) | Strong fit if you want lineage from raw notes → chunks → eval scores. |
| **Temporal**        | Need durable, resumable workflows           | Workflow code in Python; Temporal handles retries and resume after crash. |
| **Kestra**          | YAML-first; you already write YAML for chunkshop | Pleasant fit — kestra triggers run a `chunkshop ingest` on a schedule.  |
| **n8n**             | Low-code, GUI-driven                        | Good for "webhook → run chunkshop → notify Slack" demos.              |

### CDC and change-data taps for Postgres

| Tool                | When                                        | Notes                                                                 |
|---------------------|---------------------------------------------|-----------------------------------------------------------------------|
| **Debezium**        | OSS, Kafka-based, production-grade          | Real-time CDC; needs Kafka (or Kafka Connect via Strimzi).            |
| **`pgoutput` / `wal2json`** | Bare metal, roll-your-own           | Lightest Postgres-native option; you write the consumer.              |
| **Estuary Flow**    | Managed CDC, Postgres → many targets        | Pay for ops; very fast onboarding. Postgres-native source connector.  |
| **Fivetran (HVR)**  | Enterprise, lots of source systems          | Paid; mature. Drops changes into a destination table — point chunkshop at that. |
| **AWS DMS**         | AWS shop, Postgres → S3 or Postgres         | Managed service. Replicates into S3 (Pattern E) or another Postgres (Pattern D). |
| **Supabase Realtime** | If your Postgres IS Supabase               | Built-in; subscribe to row changes via websocket; thin worker forwards to staging. |
| **Materialize / RisingWave** | Want streaming SQL on top of CDC   | Lets you materialize a denormalized view of "the doc as the LLM should see it" before chunkshop touches it. Heavy but powerful. |

### Queues and durable buffers (Pattern C, scaled)

| Tool                | When                                        | Notes                                                                 |
|---------------------|---------------------------------------------|-----------------------------------------------------------------------|
| **Filesystem inbox** | Single host                                | Pattern C as written. Cheapest possible.                              |
| **Amazon SQS / SNS** | AWS shop                                   | Cheap. Pair with Lambda or a tiny consumer that writes to S3 → Pattern E. |
| **Apache Kafka**    | High volume, multi-consumer, replay needed   | Heavy. Worth it if you have other consumers besides chunkshop.        |
| **Redis Streams**   | Already running Redis                       | Lightweight. Persistent. Watch out for memory limits at scale.        |
| **RabbitMQ**        | Existing rabbit shop                        | Mature, reliable. Pair with a small consumer that writes files for chunkshop. |
| **Cloudflare Queues / R2** | Cloudflare shop                       | R2 + queues = Pattern E with no AWS bill.                             |

### Observability around chunkshop runs

| Tool                | What you get                                 |
|---------------------|----------------------------------------------|
| **`heartbeat_every` in `runtime`** | Per-doc progress lines in chunkshop's own log. |
| **Postgres `count_docs()`** | `chunkshop.PgVectorSink.count_docs()` from a sibling psql session = live doc count. |
| **OpenTelemetry**   | Wrap `chunkshop ingest` with `opentelemetry-instrument` for spans + log correlation. |
| **Sentry / Honeybadger** | Cron-level error alerting; the wrapper catches non-zero exits. |

---

## When you do **not** want any of this

- **One-shot or rarely-changing corpora** — just run `chunkshop ingest` manually when the corpus changes. No cron, no watermark.
- **Realtime UI** (sub-second from edit to retrievable) — chunkshop has minutes-of-latency, not milliseconds. The embedder + the upsert are not designed for per-keystroke ingest.
- **Per-record, micro-batch streams** — if your "doc" is a single sales note, fine. If it's a single line-item event and you have 10⁴/sec, batch them upstream first. chunkshop's per-doc transaction overhead is not free.
- **Append-only logs you query as logs** — if you're not doing semantic retrieval over the data, chunkshop is the wrong tool. A timestamped Parquet file is cheaper.

---

## Reference: the relevant config knobs

```yaml
target:
  mode: append                    # or create_if_missing for first run
  source_tag: sales_notes         # required for append; appears in `source` column
  delete_orphans: true            # NEW: per-doc shrink cleanup, same-tx as upsert
  force_overwrite: false          # bypass the "different source_tag" check on overwrite

source:
  type: pg_table                  # Patterns A, B, D
  where: "updated_at > NOW() - interval '15 minutes'"   # the delta predicate

# OR
source:
  type: files                     # Pattern C
  glob: /var/inbox-batches/CURRENT/**/*.md

# OR
source:
  type: s3                        # Pattern E
  bucket: notes-raw
  prefix: "${YEAR}/${MONTH}/${DAY}/${HOUR}/"

# OR
source:
  type: inline                    # Pattern F (Python `chunkshop.Pipeline`, Rust `chunkshop::Pipeline`)
```

Plus:
- the wrapper script: [`scripts/run_incremental_watermark.py`](../scripts/run_incremental_watermark.py)
- the pg_table sample (with runnable demo): [`docs/samples/incremental-pg-table/`](samples/incremental-pg-table/)
- the inline-mode samples (Python + Rust): [`docs/samples/inline-mode/`](samples/inline-mode/)

## Agent memory (SP-A)

Two-cell incremental pattern for agent conversation memory. Your agent writes raw
turns to a staging table, then two scheduled chunkshop cells consume them:

```python
from chunkshop.memory import ensure_staging_table, stage_event
ensure_staging_table(dsn, table="chunkshop_staging")
stage_event(dsn, session_id="s1", seq=1, role="user", content="...",
            table="chunkshop_staging")
```

- **realtime** — run frequently (e.g. every minute) so new turns are searchable fast:
  `chunkshop ingest --config src/chunkshop/configs/memory/realtime.yaml`
  writes `tier=provisional` rows.
- **consolidate** — run nightly via external cron; segments quiet sessions into
  episodes, extracts facts, and supersedes the provisional rows:
  `chunkshop ingest --config src/chunkshop/configs/memory/consolidate.yaml`

Both presets read the DSN from `${CHUNKSHOP_MEMORY_DSN}`. There is no daemon —
schedule the two `ingest` invocations externally, same as every other pattern above.
Design rationale and the pg-raggraph fact contract:
[`docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md`](superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md).

**Reading the consolidated store back out:** `chunkshop.memory.read_pre_chunked(dsn)`
yields one record per session in the exact shape pg-raggraph's
`GraphRAG.ingest_records()` accepts — episode chunks become `pre_chunked`
entries, fact triples become `known_relationships`, and O2 (consolidated-wins)
is enforced by default. End-to-end example:
[`docs/samples/memory-to-pgraggraph/`](samples/memory-to-pgraggraph/).

**Architecture write-up:** [`docs/architecture/memory-sink.md`](architecture/memory-sink.md) —
two-tier semantics, row identity, late-event rebuild (O1), crash-safety
(O3), the consolidator seam, and the pg-raggraph fact contract.
**Scheduling patterns:** [`docs/samples/memory-scheduling/`](samples/memory-scheduling/) —
cron + systemd timer, k8s CronJob, in-process Python (asyncio),
in-process Rust (tokio).

### Rust port (RM-A, tracked at chunkshop#9)

The Rust crate has the same two-cell pattern from chunkshop-rs 0.4.5+
(unreleased). Same `agent_memory.memory` schema, same `chunkshop_staging`
table, same YAML preset shape — `event_id` is **byte-identical** across
languages so an event staged from Python and re-staged from Rust hits
the same row.

```bash
export CHUNKSHOP_MEMORY_DSN="postgresql://localhost/agent_memory"

# Realtime cell (provisional tier) — run every minute
chunkshop-rs ingest --config rust/chunkshop/configs/memory/realtime.yaml

# Consolidate cell (consolidated tier, supersede=true) — run nightly
chunkshop-rs ingest --config rust/chunkshop/configs/memory/consolidate.yaml
```

The Rust API surface mirrors Python's: `chunkshop::memory::stage_event` /
`stage_events` for the staging side, `SessionStagingSource` /
`SessionEpisodeFramer` / `ConsolidationChunker` / `MemorySink` as the
provider types. Custom (LLM, rule-based) consolidators are wired at
compile time via the `Consolidator` trait — Rust has no equivalent of
Python's dynamic `module:`/`function:` callable, so the consolidator
section of the YAML uses a built-in `mode:` (currently only `extractive`).

A `read_pre_chunked` equivalent is out of scope for RM-A — defer to RM-B
if/when a Rust consumer of `agent_memory.memory` surfaces (the current
consumer story is pg-raggraph in Python).
