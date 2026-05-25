# HANDOFF — Connector Plugin Foundation program (SP-1/2/3)

**Date:** 2026-05-25
**Author:** autonomous build session (interrupted by weekly token limit)
**Branch in flight:** `feat/connector-foundation` (worktree `/home/yonk/yonk-tools/chunkshop-sp1`)

## TL;DR of where things are

The 3-sub-project plan is fully designed and planned. **SP-1 is ~95% implemented and the branch is green** (529 passed, 89 skipped; the only 6 failures are the pre-existing `lede`-extra import issue documented in CLAUDE.md — present on `main` too, unrelated). Two review findings remain to fix, then a final review + merge. SP-2 and SP-3 are planned but **not started**.

## Artifacts (all committed on `main` already)

- **Spec:** `docs/superpowers/specs/2026-05-25-chunkshop-connector-plugin-foundation-design.md` — the program design + all 10 settled decisions (D1–D10). Read §4 for SP-1's intended API; §2 for the decisions; §3 for the decomposition.
- **Plans:** `docs/superpowers/plans/2026-05-25-sp1-connector-plugin-foundation.md` (19 tasks), `...-sp2-chunkshop-connectors-bulk-port.md` (13 tasks), `...-sp3-files-rich-parsing.md` (9 tasks).

## SP-1 status (on branch `feat/connector-foundation`, NOT merged)

**Done + committed (19 tasks + 1 review fix):** sync Protocols (`SyncMode`, `IncrementalSource`, `PrunableSource`, `Document.fingerprint`, `StaleCursorError`) in `sources/base.py`; connector entry-point registry (`sources/registry.py`); generic `ConnectorSource` config + `SyncSettings` + wired into `load_source`; `RawStore` primitive (`raw_store/` — protocol + local + s3 backends + `load_raw_store` factory + `RawStoreConfig` union); OAuth interfaces (`oauth/` — `OAuthTokens`, `OAuthProvider`, `OAuthTokenStorage`, `proactive_refresh`, `MockOAuthProvider`); test helpers (`testing/` — `assert_cursor_advances`, `assert_idempotent_on_re_emit`, `mock_oauth_provider`); s3 ETag-cursor + pg_table updated_at-cursor proofs; example sync loop at **`python/examples/sync_loop.py`** (note: under `python/`, not repo-root, because the test's `parents[2]` resolves there); cookbook docs.

**Review done** (opus code-reviewer) found 3 issues. Status:

1. ✅ **FIXED + committed** — *critical*: `cursor_from` cursor semantics. S3's cursor is a `{key:etag}` map but the example loop/helpers were replacing the cursor with `cursor_from(docs[-1])`, re-emitting all-but-one object every run. **Decided design: merge-delta semantics** — `cursor_from` returns a per-doc DELTA; consumers merge each emitted doc's delta into the running cursor (`next = dict(prev); for d in docs: next.update(source.cursor_from(d))`). Fixed in `base.py` docstring, `testing/__init__.py`, `examples/sync_loop.py`, and `test_s3_incremental.py`. Real `S3Source` now passes the helpers.

2. ❌ **NOT FIXED (next session)** — *important*: `pg_table` cursor uses strict `WHERE updated_at > %s ORDER BY updated_at`. Rows sharing the boundary timestamp get silently dropped across runs. **Decided fix: tuple cursor** — cursor `{"after_ts","after_id"}`, query `WHERE (updated_at_col, id_col) > (%s, %s) ORDER BY updated_at_col, id_col`, `cursor_from(doc)` returns `{"after_ts": <doc updated_at iso>, "after_id": doc.id}`. A subagent attempted this; the attempt was BROKEN (re-emitted instead of advancing — 3 failing tests) and was **reverted** to keep the branch green. `pg_table.py` + `test_pg_table_incremental.py` are back at the last-green strict-`>` version. Redo cleanly: the likely bug in the reverted attempt was a cursor-key/param mismatch between `empty_cursor`/`iter_changes_since`/`cursor_from` — write the duplicate-timestamp test FIRST and make it pass.

3. ❌ **NOT FIXED (next session)** — *important*: `oauth/refresh.py` `proactive_refresh` does `datetime.now(timezone.utc) - tokens.expires_at`, which raises `TypeError` if `expires_at` is naive. Fix: normalize naive → UTC at the top (`exp = tokens.expires_at.replace(tzinfo=timezone.utc) if tokens.expires_at.tzinfo is None else tokens.expires_at`). Add a naive-datetime test.

**Also still pending for SP-1:** after fixing #2 and #3, add the spec-§6-mandated test running the real `PgTableSource` through `assert_idempotent_on_re_emit` (DB-backed, must PASS not skip); re-run the opus final review; then `superpowers:finishing-a-development-branch` to merge `feat/connector-foundation` → `main`.

## SP-2 status: NOT STARTED

Plan: `...-sp2-chunkshop-connectors-bulk-port.md`. **Before executing, do task #2: re-read the spec + the LANDED SP-1 code and reconcile the SP-2 plan** (registry signature `load_connector(name, config)`, `IncrementalSource`/`RawStore` shapes, the merge-delta `cursor_from` contract, `ConnectorSource` config). SP-2 Task 0 must first LOCATE the RAGFlow checkout on this Linux host (the brief's path is a macOS path — likely absent; may need `git clone https://github.com/infiniflow/ragflow`). Many SP-2 tasks are `[READ-AT-EXEC]` (lifted code can't be pre-written).

## SP-3 status: NOT STARTED

Plan: `...-sp3-files-rich-parsing.md`. Independent of SP-2; can run in parallel. Fully detailed TDD; no RAGFlow dependency.

## Environment notes

- Worktree `/home/yonk/yonk-tools/chunkshop-sp1`, branch `feat/connector-foundation`, deps synced (`uv sync --extra dev --extra extractors --extra all-backends`).
- Run tests from `python/` with `uv run --no-sync pytest -q`. The Postgres test DB (`localhost:5434`) was up this session via `docker compose -f docker-compose.test.yaml up -d` (clickhouse port 8124 conflicted with a pre-existing container — harmless; CH matrix cells just skip).
- `ruff` is not in the env; lint via `uvx ruff check <files>`.
- The 6 `lede` failures are expected unless you `uv pip install -e ".[lede]"` per CLAUDE.md.

## Execution method used

`superpowers:subagent-driven-development` — SP-1's 19 tasks were run as 6 cohesive same-file groups (each a fresh general-purpose subagent doing strict TDD per the plan), with diffs spot-checked between groups and a full opus code-review at the end. Continue the same way.
