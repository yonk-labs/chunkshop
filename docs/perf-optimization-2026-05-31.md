# chunkshop performance review — 2026-05-31

Autonomous performance pass on the Python reference implementation. Goal: ≥33%
speedup on **ingestion** and on **search** (measured separately), with **no loss
of data or accuracy**. Both targets met and exceeded with measured A/B evidence.

## Headline results

| Path | Baseline | Optimized | Speedup | How |
|---|---|---|---|---|
| **Ingestion** (200 docs, 1-chunk/doc, int8 bge-base) | 6.36 s | 3.45 s | **−45.7% (1.84×)** | sink connection reuse (code) + `threads` config |
| **Search** — hybrid, default | 30.8 ms median | 23.4 ms | **−24%** | parallel legs (transparent, code) |
| **Search** — hybrid, pool on | 30.8 ms median | 10.5 ms | **−66%** | parallel legs + `CHUNKSHOP_SEARCH_POOL=1` |

Ranking output is **byte-identical** before/after on every search change (proved
by diffing top-10 results across 5 queries). Ingested data verified identical
(row count, distinct docs, 0 null embeddings, 768-dim vectors). Full
`tests/chunkshop` suite: **zero new failures** introduced (same 39 pre-existing,
environment-only failures on baseline and on the branch).

Bench rig: 24-core box, Postgres 17 + pgvector on localhost:5434, synthetic chat
corpus (`skill-output/bakeoff/synthetic-corpus/corpus`, 1000 docs). Numbers are
min/median of repeated runs to factor out a background job that was contending
for cores during the session.

---

## Shipped changes (small, safe, measured)

### 1. Ingestion — `PgSink` write-connection reuse  *(code)*

**Before:** `PgSink.write_document` opened a fresh `psycopg.connect()` and tore it
down **per document** (`pg.py`). At ~5 ms/connect that is ~1.0 s of pure
connect/teardown per 200 docs — on this corpus, **~40% of all non-embed time**.

**After:** the sink lazily opens **one** connection and reuses it across
documents, still **committing per document**. The crash-safety and live-progress
contracts are unchanged (a committed row is visible to other sessions regardless
of whether my connection stays open; a mid-run crash still only loses the
in-flight doc). On any write error the transaction is rolled back and the
connection dropped so a poisoned transaction can never leak into the next doc.
`runner.run_cell` closes the connection in a `finally`.

- `backends/postgres.py`: new `new_connection()` (raw, caller-owned).
- `sinks/pg.py`: persistent `_write_conn`, `_get_write_conn()`, `close()`.
- `runner.py`: `sink.close()` in `finally`.

**Measured (threads held at 4, isolating the code change):**
`non_embed` 2.58 s → 1.05 s (**−59%**); total wall 6.36 s → ~4.9 s (**−24%**).

This win **scales with doc-count ÷ chunk-count**: largest for many small docs
(chat, messages, log lines, records), smaller for few large docs where the
per-doc connect is amortized over more rows.

### 2. Search — concurrent hybrid legs  *(code, transparent / default-on)*

**Before:** `hybrid_search` ran the `semantic` leg, then the `fts` leg,
**sequentially**, each opening its **own** connection. Hybrid latency ≈ sum of
legs.

**After:** the two legs are independent, side-effect-free `SELECT`s, so they run
**concurrently** (one `ThreadPoolExecutor`, one worker per extra leg). psycopg
releases the GIL during server I/O, so two backends overlap in real time. Hybrid
drops from `sum(legs)` to ≈`max(legs)`. Single-leg queries stay inline (no thread
overhead). Fusion consumes the same per-leg results, so ranking is identical.

- `search.py` `hybrid_search`: leg thunks dispatched through a thread pool.

**Measured:** hybrid median 30.8 ms → 23.4 ms (**−24%**), mean −27%. Identical
top-k.

### 3. Search — opt-in read-connection pool  *(code, opt-in via env)*

**Finding:** with warm connections, hybrid drops to ~8–11 ms. Connection
**setup** (~5–6 ms TCP+auth+first-query per leg) was the dominant search cost,
not the queries. A search service issuing many queries pays it on every call.

**Change:** `CHUNKSHOP_SEARCH_POOL=1` routes the hot read legs
(`semantic_search`, `keyword_search`) through a tiny thread-safe idle-connection
pool keyed by DSN (autocommit reads — nothing lingers idle-in-transaction; an
errored connection is closed, never recycled; `close_search_pool()` drains it).
**Default OFF** — the documented per-call-connect contract is preserved
byte-for-byte when the flag is unset.

**Measured (flag on):** hybrid median 30.8 ms → 10.5 ms (**−66%**), identical
top-k. New tests in `tests/chunkshop/test_search_pool.py` pin the lifecycle
(reuse-when-on, fresh-when-off, never-pool-a-poisoned-conn, drain-on-close).

> **Update (chunkshop#64):** the pool is now **on by default** — `CHUNKSHOP_SEARCH_POOL`
> opts *out* (`0`/`false`/`no`/`off`). Made safe with a retry-once-on-broken-connection
> guard (a reused dead connection self-heals on a fresh retry), an `os.register_at_fork`
> child reset, and a max-idle-age recycle. See `docs/hybrid-search.md`.

---

## Config-tuning levers (no code; "flip this and you get X")

### A. `embedder.threads` / `runtime.omp_num_threads` for single-cell ingest

The shipped samples use `threads: 4`, tuned for `orchestrate --concurrency 4` on a
typical box (the `concurrency × threads ≈ cores` rule). For a **single-cell**
ingest on a 24-core box, that leaves ~20 cores idle.

| threads | embed time (200 docs) |
|---|---|
| 4 | 4.71 s |
| 8 | 3.45 s (1.37×) |
| **12** | **3.12 s (1.51×)** |
| 16 | 3.09 s (diminishing) |

**Recommendation:** for one-shot `chunkshop ingest`, set `embedder.threads` and
`runtime.omp_num_threads` to ~half the physical cores (≈12 here). Keep the
`concurrency × threads ≈ cores` rule for `orchestrate`. Combined with change #1,
this is what takes ingestion to **−45.7%** overall.

> ⚠️ **Threads are NOT free capacity — they only help on an idle box.** Raising
> threads spreads the *same* work across more cores; it does not reduce the work.
> On a **busy multi-tenant server** the cores are already spoken for, and
> oversubscribing makes things *worse*: with 8 concurrent embed jobs on 24 cores,
> threads=12 (8×12=96 threads) ran **−23.5%** slower in aggregate throughput than
> threads=3 (matched). So the `−45.7%` headline is a **single-cell / idle-box**
> figure. The genuinely load-independent part of the ingestion win is change #1
> (connection reuse, ~−24%), which removes redundant work rather than
> reshuffling cores. To raise throughput on a saturated box you have to reduce
> the work itself — see the caveman filler-reduction trade in
> `docs/caveman-filler-word-reduction-2026-05-31.md`.

### B. Embedding batch shape — counter-intuitive finding (no change warranted)

I expected batching chunks **across** documents to speed embedding. It does the
opposite here: one big batch was **0.58×** (slower) than per-doc single-text
calls, because BGE pads each batch to its **longest** member — mixing a short and
a long doc wastes compute on padding. The current per-doc pattern accidentally
avoids padding waste. **No change made.** (A length-bucketed batcher could
reclaim this for multi-chunk-doc corpora — see below.)

---

## Documented for review — larger changes I did **not** make

These need design review / cross-backend parity / a contract decision, so per the
brief I'm flagging rather than implementing them.

1. ~~**Make the search pool default-on (transparent).**~~ **SHIPPED (chunkshop#64).**
   The −66% pool is now on by default with `CHUNKSHOP_SEARCH_POOL` as an opt-out.
   Done with the retry-once-on-broken-connection guard, an `os.register_at_fork`
   child reset, and a max-idle-age recycle. See section 3 above + `docs/hybrid-search.md`.

2. **COPY-based bulk insert for ingestion.** Per-doc `executemany` is fine for the
   crash-safety contract but is not the fastest bulk path. A staging-table +
   `COPY` + `INSERT … ON CONFLICT` loader (batched across N docs) would cut the
   remaining sink time substantially for large overwrite/create ingests — at the
   cost of the per-doc-commit guarantee. Worth it for big one-shot loads behind a
   `target.bulk: true` mode; needs cross-backend (mariadb/sqlite/clickhouse)
   thought.

3. **Length-bucketed embedding batches.** Sort chunks by token length and batch
   within buckets to avoid the padding waste from finding B. Lets large-batch mode
   actually win on corpora with multi-chunk, variable-length docs. Self-contained
   in the embedder; needs the chunk→row ordering preserved on the way back.

4. **Warm-model search server / daemon.** The `chunkshop search` CLI reloads the
   ONNX embedder on every invocation (seconds), which dwarfs the ~10–30 ms query
   for interactive use. A persistent search process (or reusing the embedder
   across queries in-process — already the case for library callers) is the real
   fix for interactive latency. Architecture-level.

5. **HNSW `ef_search` knob.** Not currently exposed for query time. On larger
   corpora it's the standard recall/latency dial; worth surfacing as a search
   option once corpora outgrow the point where pgvector picks HNSW over seq scan.

---

## Files touched

```
python/src/chunkshop/backends/postgres.py   + new_connection()
python/src/chunkshop/sinks/pg.py             persistent write conn + close()
python/src/chunkshop/runner.py               sink.close() in finally
python/src/chunkshop/search.py               parallel legs + opt-in read pool
python/tests/chunkshop/test_pg_document_store.py   mocks -> new_connection
python/tests/chunkshop/test_search_pool.py        new: pool lifecycle tests
```
