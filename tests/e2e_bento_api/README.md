# tests/e2e_bento_api/ — 3rd-party E2E: bento-api consuming chunkshop

The third test tier: does a real downstream product (bento) get correct
behavior out of chunkshop when it's wired in as a library dependency behind
its `bento-chunkshop-shim` service, through bento's own public API
(`bento-api`, published at `:8000`)?

## What this exercises

`POST /v1/ingest` on `bento-api` (`backend/api/routes/ingest.py`) is a
5-step orchestration where step 1 is chunkshop:

1. `chunkshop-shim POST /v1/ingest` — writes chunkshop's own debug chunks table.
2. bento-api reconstructs full documents from that table.
3. A stele artifact per document.
4. `pg-raggraph-proxy POST /v1/ingest` (Pattern D, chunkshop-driven chunk
   strategy) — writes the `public.documents` / `public.chunks` tables that
   `/v1/ask` actually reads.
5. A stele memory per artifact.

This script drives that whole chain with a throwaway 2-document corpus and
asserts each stage's output count, then tears everything down.

## Isolation

- A dedicated KB is created via `POST /v1/me/kbs` (authenticated as the
  bootstrap admin via the `LOCAL_ADMIN_ENABLED` dev bypass — see below), so
  every downstream write lands under that KB's own `kb-<uuid>` namespace,
  never the shared/anonymous legacy `"default"` namespace other KBs
  (including ones used by other benchmark/test efforts) live in.
- chunkshop's own debug table is named `test_cs_<timestamp>_chunks` in the
  `public` schema (bento-api's document-reconstruction step assumes
  `public`, unqualified — a custom schema silently queries the wrong table;
  see the script comment above `TARGET_TABLE`).
- Teardown (`trap ... EXIT`, always runs): deletes the KB, manually deletes
  the `documents`/`chunks` rows under its namespace (KB deletion was
  observed to NOT reliably cascade this — see "Known gaps" below), drops
  the chunkshop debug table, removes the staged upload file, and revokes
  the minted API key.

## Requires

- `bento-api`, `bento-chunkshop-shim`, `bento-stele-shim`,
  `bento-pg-raggraph-proxy`, `bento-postgres` all running (this repo starts
  none of them).
- `LOCAL_ADMIN_ENABLED=true` + `LOCAL_ADMIN_PASSWORD` set in `bento-api`'s
  environment (a documented dev-only OAuth bypass, `routes/admin_local_login.py`).
  Without it there is no way to reach an *isolated* per-KB ingest path
  without real OAuth credentials this repo doesn't have — the script skips
  cleanly (exit 0) rather than falling back to the shared anonymous
  namespace.

## Running

```bash
export LOCAL_ADMIN_PASSWORD=bento-local-dev   # must match bento-api's env
bash tests/e2e_bento_api/run-all.sh
```

## Known gaps found while building this (not fixed here — out of scope)

- **KB deletion doesn't cascade pg-raggraph rows.** `DELETE /v1/me/kbs/{id}`
  returned 204, but `public.documents`/`public.chunks` rows under that KB's
  namespace were still present afterward (verified via direct query) — the
  teardown here deletes them explicitly. Worth a look in the bento repo:
  either the cascade is conditional on something this KB didn't trigger, or
  it's missing entirely.
- **`kb_id`-less / `LEGACY_KB_ID`-sentinel ingest writes to a shared
  `"default"` namespace** that other KBs (including other benchmark
  efforts) may use — this script deliberately avoids that path entirely by
  always creating a fresh authenticated KB first.
