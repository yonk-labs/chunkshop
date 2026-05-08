# Cross-Language MariaDB Vector Parity Walkthrough

**Date:** 2026-05-07 (R2 ship date — refresh on re-run)
**Purpose:** Manual proof that vectors written by Python's `MariaDbSink` are byte-for-byte readable and queryable by the Rust `MariadbSink`.

## Setup

```bash
docker compose -f docker-compose.test.yaml up -d mariadb
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"

# Python venv with the mariadb extra
cd python
uv sync --extra dev --extra extractors --extra mariadb
```

## Step 1 — Python writes 5 chunks via `MariaDbSink`

```bash
cd /home/yonk/yonk-tools/chunkshop
uv --project python run python python/scripts/seed_mariadb_cross_lang_fixture.py
# → Seeded 5 chunks into chunkshop_xlang.parity
```

## Step 2 — Inspect rows directly

```bash
docker exec chunkshop-v4-mariadb-1 mariadb -uroot -prootpw chunkshop_xlang \
  -e "SELECT id, doc_id, seq_num, source FROM parity ORDER BY doc_id"
```

Expected:
```
id                 doc_id        seq_num  source
doc-alpha::0       doc-alpha     0        cross_lang_fixture
doc-bravo::0       doc-bravo     0        cross_lang_fixture
doc-charlie::0     doc-charlie   0        cross_lang_fixture
doc-delta::0       doc-delta     0        cross_lang_fixture
doc-echo::0        doc-echo      0        cross_lang_fixture
```

## Step 3 — Rust queries top-K via the same vectors

```bash
cd rust
cargo test -p chunkshop-rs --test mariadb_cross_lang_parity -- --nocapture 2>&1 | tail -5
```

Expected: `1 passed`. The test asserts position 0 is `doc-alpha::0` for the one-hot alpha query; positions 1–4 form the orthogonal set `{bravo, charlie, delta, echo}` (sub-ordering is implementation-defined when distances tie).

## Step 4 — Raw-SQL distance check (independent cross-check)

```bash
docker exec chunkshop-v4-mariadb-1 mariadb -uroot -prootpw chunkshop_xlang -e "
SELECT id, ROUND(VEC_DISTANCE_COSINE(embedding,
  VEC_FromText('[1.000000,0.000000,0.000000,0.000000,0.000000,0.000000,0.000000,0.000000]')), 6) AS d
FROM parity ORDER BY d LIMIT 5"
```

Expected first row: `doc-alpha::0  0.000000` (perfect cosine match). Rows 2–5 all have distance `1.000000` (orthogonal one-hot embeddings).

## Step 5 — End-to-end sample-mariadb.yaml ingest (SC-007)

For a non-toy run that actually exercises the embedder + chunker:

```bash
export CHUNKSHOP_DSN="mysql://root:rootpw@localhost:3307/chunkshop_samples"
docker exec chunkshop-v4-mariadb-1 mariadb -uroot -prootpw -e \
  "CREATE DATABASE IF NOT EXISTS chunkshop_samples"

cd /home/yonk/yonk-tools/chunkshop
cargo run -p chunkshop-rs --release -- ingest --config docs/samples/sample-mariadb.yaml
```

Expected end-of-run line:
```
cell samples_mariadb_demo DONE docs=4 chunks=13 wall=~1s
```

Verify rows landed:
```bash
docker exec chunkshop-v4-mariadb-1 mariadb -uroot -prootpw chunkshop_samples \
  -e "SELECT COUNT(DISTINCT doc_id), COUNT(*) FROM handbook"
# → 4    13
```

## Conclusion

Python and Rust agree on:
- vector storage format (`VEC_FromText('[...]')` text input)
- ID convention (`{doc_id}::{seq_num}`)
- query semantics (`VEC_DISTANCE_COSINE` ordering)
- row schema (id + doc_id + seq_num + original_content + embedded_content + tags JSON + metadata JSON + embedding VECTOR + source + created_at)

R2-SC-004 satisfied — cross-language vector parity verified by:
- automated Rust integration test (`tests/mariadb_cross_lang_parity.rs`) — SC-004(a)
- this manual walkthrough — SC-004(b)
- end-to-end sample-mariadb.yaml ingest — SC-007
