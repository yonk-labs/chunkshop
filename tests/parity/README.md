# tests/parity/ — 3-mode ingest/chunking parity (no-regression)

Proves chunkshop produces the **same chunk set** from one fixed source
document whether it is invoked as:

1. **EMBEDDED** — the `chunkshop` Python package in-process (`run_cell`).
2. **SERVICE** — `bento-chunkshop-shim POST /v1/ingest` (via `docker exec curl`).
3. **BENTO** — `bento-api POST /v1/ingest` orchestration (isolated KB).

The shim and bento expose **ingest only** (retrieval is pg-raggraph's job
downstream), so this is **chunking parity, not retrieval parity**.

## Fairness

One identical chunker/framer/embedder config (`sentence_aware`, max_chars=300)
is used in all three modes — embedded parses it into `CellConfig`, shim and
bento receive the byte-identical YAML string. Only `target` (which
DB/schema/table) and `source.path` (host copy vs the shim's uploads-volume copy
of the **same bytes**) vary; chunking depends on neither. `sentence_aware` is
purely lexical, so its output is embedder-independent — the embedder cannot
affect the gate.

## What the strict gate covers vs what's informational

- **STRICT GATE (asserted): EMBEDDED == SERVICE == BENTO_shim.** All three are
  chunkshop chunking the identical doc with our config. `BENTO_shim` is the
  chunkshop debug table bento-api writes in step 1 (it forwards our cell_yaml
  verbatim to the shim) — chunkshop-inside-bento. A divergence here is a real
  regression.
- **INFORMATIONAL (reported, not gated): BENTO_public** = bento's
  `public.chunks`, written by **pg-raggraph** (step 4). pg-raggraph is a
  different tool; bento reconstructs full documents from chunkshop's chunks
  (step 2) and pg-raggraph re-chunks them at the KB's own chunk sizing — so
  `public.chunks` is expected to differ, and we say so rather than force a
  match. `public.chunks.metadata.chunkshop_seq_num` is used to align rows back
  to chunkshop when it IS a pass-through.

## Running

```bash
export LOCAL_ADMIN_PASSWORD=bento-local-dev   # bento-api dev-auth for the isolated KB
cd python && uv run --no-sync python ../tests/parity/ingest_parity.py
```

Exit 0 = gate PASS, 1 = a mode diverged, 2 = inconclusive (<2 modes ran).
The BENTO mode skips cleanly (EMBEDDED+SERVICE still run and gate) if
`LOCAL_ADMIN_PASSWORD` is unset or bento-api's local-admin bypass is off.

## Isolation

All doc-ids are `test_cs_parity_*`. Teardown (finally block, always) drops the
embedded schema (on `chunkshop-test-pg`), the shim/bento schemas + bento's
chunkshop debug table (on `bento-postgres`), deletes the KB **and its
pg-raggraph `documents`/`chunks` rows explicitly** (KB-delete does not cascade
them — see `../e2e_bento_api/README.md`), removes the staged upload file, and
revokes the minted API key. Never restarts/reconfigures a shared container.

## Requires

`bento-chunkshop-shim`, `bento-postgres` (published on host `:5433`), and a
throwaway `chunkshop-test-pg` (host `:5533`) for the embedded sink. BENTO mode
additionally needs `bento-api` (`:8000`) + `bento-pg-raggraph-proxy` +
`bento-stele-shim` running, and the `LOCAL_ADMIN_ENABLED` dev bypass.
