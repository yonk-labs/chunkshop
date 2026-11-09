# tests/service_shim/ — bento-chunkshop-shim contract smoke test

Exercises the `bento-chunkshop-shim` FastAPI service (the bento repo's HTTP
wrapper around this library) the way a real BFF caller does: `/v1/ingest`,
`/v1/delete-by-doc-ids`, `/health`, and `/v1/bakeoff/{run,leaderboard}`.

Not a replacement for `python/tests/` (library-level) or `tests/sub/`
(CLI-level) — this is the third tier: does the *service contract* hold when
a request goes over HTTP into a container that vendors this library?

## Why `docker exec`, not a normal HTTP client

The shim container is internal-only (no published host port) by design —
it's reachable from other containers on the bento Docker network, or via
`docker exec <container> curl ...` from the host. This script uses the
latter since it runs outside that network.

## Isolation

All writes go to a dedicated Postgres schema named `test_cs_<unix timestamp>`
inside the shared `bento-postgres` instance (reusing the shim's own
`DATABASE_URL`, since that's the only DSN the shim's environment resolves).
The script drops that schema and removes its staged upload file on exit
(`trap ... EXIT`), success or failure. It never touches any other schema.

## Running

```bash
bash tests/service_shim/run-all.sh
```

Skips cleanly (exit 0) if `bento-chunkshop-shim` isn't running — this talks
to a shared dev-environment container this repo does not own or start.

## Known gap this test surfaces (not fixed here)

`bakeoff_runs` / `bakeoff_results` are BFF-owned ledger tables
(`backend/docker/postgres/init/02_bff_tables.sql` in the bento repo). If
they haven't been migrated into the target `bento-postgres` instance, the
unguarded ledger writes in `/v1/bakeoff/run` and `/v1/bakeoff/leaderboard`
(unlike the best-effort-guarded `ingest_jobs` writes) raise an uncaught
`UndefinedTable` and the shim replies with a bare `500 Internal Server
Error` instead of the documented `{"code": ..., "detail": ...}` JSON shape.
This script detects and reports that condition rather than treating it as a
chunkshop-shim regression — the fix belongs in the bento repo (either run
the migration, or add the same best-effort guard the ingest ledger already
has).
