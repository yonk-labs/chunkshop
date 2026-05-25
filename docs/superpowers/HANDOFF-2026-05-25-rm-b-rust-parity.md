# HANDOFF — RM-B Rust SP-1 Parity

**Date:** 2026-05-25
**Author:** Python session that shipped chunkshop v0.6.0
**Branch to create:** `feat/rm-b-rust-parity` (off `main` at the commit you check out — currently `5365249` or later)

## TL;DR

chunkshop 0.6.0 shipped a large set of Python-side primitives + source enhancements. The Rust crate's `Cargo.toml` got bumped to 0.6.0 to match versions, but the actual Rust behavior is still v0.5.0-shaped for several foundational surfaces. This handoff carries the gap analysis + paste-ready prompt to close it in a new session.

## What needs to land in Rust (high level)

| Surface | Python | Rust | Plan task |
|---|---|---|---|
| `SyncMode` enum + `IncrementalSource` / `PrunableSource` traits + `StaleCursorError` + `Document.fingerprint` | ✅ shipped in SP-1 | ❌ not present | Task 1 |
| `pg_table` tuple cursor `{after_ts, after_id}` (boundary-row safety) | ✅ commit `ff01268` | ❌ Rust pg_table still on pre-tuple cursor | Task 2 |
| `s3` ETag-based `IncrementalSource` impl | ✅ commit `f875450` + review fixes | ❌ Rust s3 still full-resync | Task 3 |
| `http` source: `crawl_depth` + `respect_robots` + ETag/Last-Modified cursor + politeness | ✅ commit `fcbad65` | ❌ Rust http still bare URL list | Task 4 |
| `RawStore` primitive + `local` + `s3` backends | ✅ shipped in SP-1 | ❌ no `raw_store` module | Task 5 |
| Cross-language parity smoke | n/a | needs test | Task 6 |
| Docs + at-a-glance table updates | n/a | needs update | Task 7 |

## What is INTENTIONALLY NOT in Rust (spec D6 — do not port)

These were Python-only by design. Don't accidentally port:

- chunkshop-connectors plugin package (gdrive, github, blob, rss, slack, notion, dropbox, gitlab, + 20 stubs)
- `chunkshop.codeparse` foundation (tree-sitter + regex)
- `code_aware` chunker (stdlib `ast`)
- `symbol_aware` chunker (depends on codeparse)
- `code_relationships` + `code_summary` extractors
- `comment_extracts` source
- OAuth providers (`GoogleOAuthProvider`, `SlackOAuthProvider`)
- File parsers (PDF/DOCX/PPTX/XLSX/HTML behind opt-in extras)
- CLI `--by-symbol` flag and `chunkshop impact-of` subcommand

## Where everything lives

- **Plan**: `docs/superpowers/plans/2026-05-25-rm-b-rust-sp1-parity.md` — 8 tasks, fully specified with file paths + signatures + test stubs.
- **Reference Python implementations** are listed per-task in the plan.
- **Existing Rust crate**: `rust/chunkshop/src/` — current source/base.rs has the `Source` trait but no cursor abstractions.
- **Existing Rust tests**: `rust/chunkshop/tests/` — strong pattern to mirror for the new tests.

## Prior art

- RM-A (Rust memory primitives — shipped 2026-05-19, merged to main): `docs/superpowers/specs/2026-05-19-chunkshop-rm-a-rust-memory-primitives-design.md`. Same shape: lift a Python-shipped feature into Rust with byte-identical cross-language behavior. RM-B follows RM-A's playbook.

## Active branches at handoff time

| Branch | State |
|---|---|
| `main` | At `5365249` or later — chunkshop v0.6.0 Python ingest enhancements shipped, Rust crate at v0.6.0 Cargo.toml but with v0.5.0 behavior |
| `feat/rm-b-rust-parity` | **TO BE CREATED** by the new session off main |

## Test stack required

```bash
# Postgres at localhost:5434, MariaDB at :3307, ClickHouse at :8124
docker compose -f docker-compose.test.yaml up -d --wait

# Rust toolchain (Cargo.toml already pins versions)
rustup show
cargo --version

# For the cross-language parity test (Task 6): both Python and Rust toolchains in PATH
```

## Open issues / Linked tickets

This is RM-B. No specific GH issue filed yet — paste this section into a new issue when opening one:

> **Title:** RM-B — Rust parity for SP-1 sync primitives + source enhancements
>
> **Body:** Python shipped these in v0.6.0 (#18, #19, #20 closed); Rust crate doesn't have them yet:
> - SyncMode + IncrementalSource + PrunableSource traits
> - pg_table tuple cursor (boundary-row safety)
> - s3 ETag IncrementalSource impl
> - http depth-crawl + ETag cursor + robots.txt
> - RawStore primitive + local + s3 backends
>
> Plan at `docs/superpowers/plans/2026-05-25-rm-b-rust-sp1-parity.md`. 8 tasks, ~2-3 days of focused work.

---

## Paste-ready resume prompt for the new session

Copy the entire block below into the new session's first message:

```
Resume the chunkshop project as a fresh session. Pick up RM-B (Rust parity for SP-1 sync primitives + source enhancements). Full context is on the main branch of /Users/matt.yonkovit/yonk-tools/chunkshop.

START by reading, in order:
  docs/superpowers/HANDOFF-2026-05-25-rm-b-rust-parity.md   (this handoff + paste prompt source)
  docs/superpowers/plans/2026-05-25-rm-b-rust-sp1-parity.md (the 8-task plan)
  CLAUDE.md                                                 (chunkshop conventions — read sections "Architecture" and "Load-bearing details")
  docs/superpowers/specs/2026-05-19-chunkshop-rm-a-rust-memory-primitives-design.md   (RM-A spec — RM-B follows the same playbook)

Create a worktree for the work:
  git worktree add ../chunkshop-rm-b -b feat/rm-b-rust-parity main
  cd ../chunkshop-rm-b

Bring the test DBs up:
  docker compose -f docker-compose.test.yaml up -d --wait

Use superpowers:subagent-driven-development OR superpowers:executing-plans. Strict TDD — each task writes its failing test first, then implements, then commits.

Tasks (per the plan, in order — Tasks 1+5 are independent and can be done in parallel worktrees if you want):
  Task 0: pre-flight audit of rust/chunkshop/src/sources/base.rs (no commit)
  Task 1: SyncMode + IncrementalSource + PrunableSource + StaleCursorError + Document.fingerprint
  Task 2: pg_table tuple cursor (depends on Task 1)
  Task 3: s3 ETag IncrementalSource (depends on Task 1)
  Task 4: http depth-crawl + ETag cursor (depends on Task 1)
  Task 5: RawStore + local + s3 backends (independent of Task 1)
  Task 6: cross-language parity smoke
  Task 7: docs + at-a-glance table
  Task 8: gate (cargo test + clippy + fmt) + merge via superpowers:finishing-a-development-branch

Reference Python implementations to mirror behavior + tests:
  python/src/chunkshop/sources/base.py             — Task 1 model
  python/src/chunkshop/sources/pg_table.py         — Task 2 cursor logic
  python/src/chunkshop/sources/s3.py               — Task 3 ETag map + merge-delta
  python/src/chunkshop/sources/http.py             — Task 4 crawl/cursor/robots
  python/src/chunkshop/raw_store/{base,local,s3}.py — Task 5 protocol + backends
  python/tests/chunkshop/test_pg_table_incremental.py     — boundary-row test
  python/tests/chunkshop/test_s3_incremental.py           — ETag-map test
  python/tests/chunkshop/test_http_crawl.py               — 17-test crawl matrix
  python/tests/chunkshop/test_raw_store_*.py              — protocol + local + s3 + factory

OUT OF SCOPE (do not port to Rust — these are Python-only per spec D6):
  - chunkshop-connectors plugin (gdrive / github / blob / rss / slack / notion / dropbox / gitlab / 20 stubs)
  - chunkshop.codeparse (tree-sitter foundation)
  - code_aware + symbol_aware chunkers
  - code_relationships + code_summary extractors
  - comment_extracts source
  - OAuth providers
  - File parsers (PDF/DOCX/etc.)

Verify each task with `cargo test --workspace`. Don't merge anything red. Final gate: clippy + fmt clean + cross-language parity passes.

When all 8 tasks land, tag rm-b-rust-sp1-parity, merge feat/rm-b-rust-parity → main, and report back with: commit SHAs, test counts before/after, any deviations from the plan, and whether to bump chunkshop to 0.6.1 (or just retire the Rust-behind-Cargo-tag flag in the v0.6.0 release notes).

Don't merge anything red. Don't push origin/main without an explicit go-ahead.
```
