# Production Readiness Audit — Fix Plan

## TL;DR
17 findings across 10 dimensions. 0 BLOCKERs — chunkshop v0.4.0 ships. 1 P0 (replace `serde_yml`), 2 P1s (default-install backend extras + per-backend lazy-import errors), 4 P2s (panic→Result, print→logging, ClickHouse default, lib structured logging), 7 P3s (docs polish + nice-to-haves), 3 NOTEs left as-is. Total P0+P1 effort: **~1 day**. Total P0–P2: **~3–4 days**. None of these block v0.4.0 in production; the P0 must be the first task in v0.4.1.

---

## Priority tiers

- **P0 — Before next release (v0.4.1):** Cannot ship v0.4.1 without these.
- **P1 — Launch week (v0.4.1):** Fix in the same release as P0; users hit them.
- **P2 — First month (v0.4.x):** Real gaps, few weeks of runway.
- **P3 — Next quarter (v0.5+):** Tracked debt.
- **P4 — Backlog:** Nice to have.

---

## P0 — Before next release (v0.4.1)

### PR-001 — Migrate off `serde_yml` to a maintained YAML parser
- **GAP-IDs:** GAP-007
- **Action:** Replace `serde_yml = "0.0.12"` in `rust/chunkshop/Cargo.toml` with `serde_yaml_ng = "0.10"` (or `serde_norway = "0.9"`). Update `use` statements (`serde_yml::*` → `serde_yaml_ng::*`). Run `cargo test -p chunkshop-rs` end-to-end; verify every YAML sample loads with identical behavior. Run `cargo audit` and confirm RUSTSEC-2025-0067 and RUSTSEC-2025-0068 are gone.
- **Effort:** S (single dep swap + import rename; `serde::Deserialize` derives unchanged)
- **Acceptance criteria:**
  - `cargo audit` reports 0 vulnerabilities (advisories only for unrelated transitive deps).
  - All 267 Rust tests pass.
  - All sample YAMLs (`docs/samples/*.yaml`) load and ingest end-to-end.
  - Bakeoff round-trip parity test (`tests/dialect_*_parity.rs`) unchanged.
- **Dependencies:** none
- **Risk if skipped:** Unsoundness in YAML parser is latent — could surface as UB at any user's site. No future security patches on `serde_yml`. Compounds with every subsequent release.

---

## P1 — Launch week (v0.4.1)

### PR-002 — Make default install include all backend drivers
- **GAP-IDs:** GAP-016
- **Action:** Update the README quickstart to `uv sync --extra dev --extra all-backends` (one line). Verify this is also reflected in `python/README.md`, `docs/getting-started.md`, and any tutorial doc that has install steps. Update CI install commands to match.
- **Effort:** XS (README + 3-4 doc edits)
- **Acceptance criteria:**
  - All install snippets across `README.md`, `python/README.md`, `docs/getting-started.md`, `docs/tutorial.md`, `docs/engines/*.md` reference the `all-backends` extra (either by default or in their own backend-specific section).
  - A fresh `uv sync --extra dev --extra all-backends` followed by `chunkshop ingest --config docs/samples/sample-mariadb.yaml` succeeds without `ImportError`.
- **Dependencies:** none

### PR-003 — Branded lazy-import errors for backend drivers
- **GAP-IDs:** GAP-016 (defense-in-depth)
- **Action:** In each `python/src/chunkshop/backends/{mariadb,sqlite,clickhouse}.py`, wrap the driver-library `import` in a try/except that re-raises with a chunkshop-branded message:
  ```python
  try:
      import pymysql
  except ImportError as e:
      raise ImportError(
          "chunkshop's MariaDB backend requires the 'mariadb' extra. "
          "Install with: pip install 'chunkshop[mariadb]' "
          "(or 'chunkshop[all-backends]')."
      ) from e
  ```
  Repeat for `clickhouse_connect`, `sqlite_vec`. Verify with a fresh venv that doesn't have the extras installed.
- **Effort:** S (3 files, ~5 lines each)
- **Acceptance criteria:**
  - Running `chunkshop ingest` on a MariaDB cell without `pymysql` installed shows the branded error, not the raw `ModuleNotFoundError`.
  - Same for ClickHouse and SQLite paths.
  - Test added: simulate missing extra (mock the import to raise) and assert the branded message.
- **Dependencies:** none

---

## P2 — First month (v0.4.x or v0.4.2)

### PR-004 — Surface `serde_yml` migration in Rust release notes + architecture doc
- **GAP-IDs:** GAP-007 (followup)
- **Action:** After PR-001 lands, add a note to `docs/architecture.md` and `python/README.md` (Rust section) confirming the YAML parser provenance and that it's maintained. Add a CHANGELOG-style entry in the v0.4.1 tag message.
- **Effort:** XS
- **Dependencies:** PR-001
- **Risk if skipped:** Users with their own audit pipelines need to know the parser changed.

### PR-005 — Replace production-code `panic!` with `Result` or `unreachable!`
- **GAP-IDs:** GAP-003
- **Action:** Change `rust/chunkshop/src/chunker.rs:369` from `panic!("max_chars must be positive")` to one of:
  - Return a `Result<Vec<String>, anyhow::Error>` and propagate up — caller-facing.
  - `unreachable!("max_chars is validated > 0 at config load")` if validation is guaranteed upstream.
  - `assert!(max_chars > 0, "max_chars must be positive")` with a `# Panics` rustdoc block.
- **Effort:** S (one-file change + ripple through callers if signature changes)
- **Acceptance criteria:**
  - No `panic!` in production code paths.
  - YAML with `max_chars: 0` rejected at config load with a clear error.
- **Dependencies:** none

### PR-006 — Switch Python lib `print()` to module-level `logging`
- **GAP-IDs:** GAP-004
- **Action:** Replace the 5 non-CLI `print()` calls (`runner.py:36`, `orchestrator.py:68,81,96,135`, `extractors/spacy_entities.py:26`) with `logging.getLogger(__name__).info(...)`. Configure the CLI entry points to wire a stdout handler at INFO level so the user-visible behavior is identical. Library mode users can configure their own loggers.
- **Effort:** S (5 call-sites + 1 CLI handler wire-up)
- **Acceptance criteria:**
  - `chunkshop ingest` CLI output is unchanged byte-for-byte.
  - Importing `chunkshop` from a host app and calling `run_cell(...)` doesn't print to stdout unless the host configured logging.
- **Dependencies:** none

### PR-007 — Default ClickHouse engine to `ReplacingMergeTree` OR warn on append without dedup
- **GAP-IDs:** GAP-015
- **Action:** Two options, pick one:
  - **Option A (low risk):** When `mode: append` is used on ClickHouse without an explicit `engine: ReplacingMergeTree(...)`, emit a one-time process-level warning: `"ClickHouse default MergeTree accumulates duplicates on re-ingest. Set engine: 'ReplacingMergeTree(created_at) ORDER BY (id)' for dedup at merge time."`
  - **Option B (behavior change):** Default `engine` to `ReplacingMergeTree(created_at) ORDER BY (id)`. Document as breaking change in v0.5.
  - **Recommendation:** Option A in v0.4.x, Option B in v0.5.
- **Effort:** S
- **Acceptance criteria:**
  - User runs `mode: append` on CH without explicit engine → warning visible.
  - User explicitly sets ReplacingMergeTree → no warning.
- **Dependencies:** none

### PR-008 — Add `chunkshop validate <yaml>` dry-run command
- **GAP-IDs:** GAP-012
- **Action:** Add CLI subcommand to both Python (`cli.py`) and Rust (`main.rs`):
  - `chunkshop validate --config x.yaml` — load + pydantic / serde validation, exit 0/1 without opening DB connections.
- **Effort:** M (2 CLI subcommands across both languages, validation already exists in `load_config`)
- **Acceptance criteria:**
  - `chunkshop validate --config docs/samples/sample.yaml` returns exit 0.
  - `chunkshop validate --config /tmp/bad.yaml` (typo, missing field) returns non-zero with the same error a real ingest would produce — without touching DBs.
- **Dependencies:** none
- **Risk if skipped:** None — current users work around by running `chunkshop ingest` and aborting after the config-load failure.

---

## P3 — Next quarter (v0.5+)

### PR-009 — Mitigate `rsa` Marvin Attack in docs
- **GAP-IDs:** GAP-008
- **Action:** Add a security section to `docs/engines/mariadb.md` documenting the Marvin Attack precondition (adversarial network + RSA auth) and recommending `mysql_native_password` or TLS termination for sensitive deployments. Pin the recommendation in the engine doc's "Troubleshooting" section.
- **Effort:** XS
- **Dependencies:** none
- **Risk if skipped:** UNLIKELY × CONTAINED. Users who need to know don't, and pick auth blindly.

### PR-010 — `chunkshop init` scaffolding command
- **GAP-IDs:** GAP-013
- **Action:** Interactive prompt: "which backend? which corpus path? which model?" → emit a minimal `cell.yaml`. Lower the bar for first-time users vs. copying from `docs/samples/`.
- **Effort:** M

### PR-011 — Bump Python classifier from "3 - Alpha" to "4 - Beta"
- **GAP-IDs:** GAP-017
- **Action:** Edit `python/pyproject.toml` — change classifier from `Development Status :: 3 - Alpha` to `Development Status :: 4 - Beta`. Reflects v0.4.0's actual test coverage + cross-language matrix.
- **Effort:** XS
- **Acceptance criteria:** PyPI page shows Beta classifier on next release.

### PR-012 — `--strict` test mode that fails on skip
- **GAP-IDs:** GAP-005
- **Action:** Add a CI gate or `pytest`-marker invariant that, in the production-test environment with all DSNs set, asserts 0 skipped tests. Today, a misconfigured CI silently skips DSN-conditional coverage.
- **Effort:** S
- **Dependencies:** CI access

### PR-013 — Document v0.3 → v0.4 upgrade path
- **GAP-IDs:** GAP-014
- **Action:** Add `docs/upgrading.md` covering: "v0.3.x → v0.4.0 has no breaking schema changes; existing PG tables work as-is. The new 4-backend story is purely additive. Existing cells re-run idempotently."
- **Effort:** XS

### PR-014 — Optional structured (JSON) logging
- **GAP-IDs:** GAP-010
- **Action:** Allow an env var or YAML field (e.g., `runtime.log_format: json`) to switch the log formatter to JSON. Useful for users running chunkshop in jobs that ship logs to a log aggregator.
- **Effort:** M (one formatter swap per language)

### PR-015 — Publish at-scale benchmarks
- **GAP-IDs:** GAP-006
- **Action:** Run chunkshop against a 1M+ doc corpus on each backend, publish throughput numbers in `docs/benchmarks-at-scale.md`. Validates and documents the operational envelope.
- **Effort:** L (corpus selection + 4 backend runs + analysis)

---

## P4 — Backlog

### PR-016 — Retry-with-backoff on transient connection errors
- **GAP-IDs:** GAP-011
- **Action:** Wrap sink connect / write_document in a retry loop with exponential backoff. Distinguish transient (TCP reset, server temporarily unavailable) from logical (auth fail, schema mismatch) — only retry the transient class.
- **Effort:** M
- **Risk if skipped:** Network blips fail the cell; idempotent reruns recover. Real but low-impact.

### PR-017 — Rust orchestrator
- **GAP-IDs:** GAP-002
- **Action:** Port `chunkshop orchestrate` to Rust. Tokio task spawn + subprocess management.
- **Effort:** XL
- **Risk if skipped:** Rust users running large multi-cell workloads must drive their own parallelism; common workaround is shelling out to Python orchestrator.

### PR-018 — Multi-target Rust bakeoff
- **GAP-IDs:** GAP-001
- **Action:** Extend Rust `BakeoffConfig` to accept `targets:` list across all 4 backends; mirror Python's bakeoff runner. Drops the legacy `schema:` field. (Identified as Wave-2 follow-up #7 during v0.4.0 work.)
- **Effort:** M

---

## Minimum Viable Ship List (P0 only)

If you only have time for one PR before v0.4.1:

- **PR-001** — Migrate off `serde_yml`. **~half day.**

That's it. P0 is one task.

---

## Sleep Well List (P0 + P1)

If you have a full day before v0.4.1:

- **PR-001** — Migrate off `serde_yml` (S)
- **PR-002** — README install includes `all-backends` (XS)
- **PR-003** — Branded lazy-import errors per backend (S)

**Total: 1 day. Closes the CRITICAL + the only SERIOUS issue with day-one user impact.**

---

## Estimated total effort by tier

| Tier | PR count | Total effort |
|---|---:|---|
| P0 | 1 | ~half day |
| P1 | 2 | ~half day |
| P2 | 5 | ~2 days |
| P3 | 7 | ~1 week |
| P4 | 3 | ~2 weeks |
| **All tiers** | **18** | **~3.5 weeks** for full polish; **~1 day** for the must-haves |

Note: PR-018 (multi-target Rust bakeoff) is already tracked separately as a Wave-2 follow-up. Most other P3/P4 items are normal v0.4.x → v0.5 evolution rather than urgent gaps.

---

## See also

- `ProdReady-GapAnalysis.md` — Findings catalog with evidence per gap.
- `ProdReady-RiskAssessment.md` — Likelihood × blast-radius scoring for SERIOUS+ items.
- `ProdReady-TaskIndex.md` — Task file index, one per PR-NNN.
- `tasks/` — Self-contained ticket files per fix.
