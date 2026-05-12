# Production Readiness Audit — Gap Analysis

## TL;DR
chunkshop v0.4.0 is a well-tested, well-documented batch ingestion tool. Functional completeness, security posture (SQL injection defended, no hardcoded secrets, no shell injection), code hygiene (0 TODO/FIXME, 0 println in lib, 1 properly-commented unsafe), and test coverage (267 Rust / 349 Python tests, cross-backend matrix pinned in CI) are all strong. The single CRITICAL is a direct YAML-parser dependency (`serde_yml` + `libyml`) flagged as unsound and unmaintained — needs migration before the next release. Two SERIOUS findings: a transitive `rsa` crate timing-sidechannel CVE with no upstream fix, and a default-install UX gap where backend extras aren't pulled in by `uv sync`.

**Verdict: READY WITH CAVEATS.** Fix CRITICAL before next release; SERIOUS items merit a fast-follow in v0.4.1.

---

## Project context

- **Shape:** CLI + library, dual-language (Python reference + Rust port).
- **Deployment target:** `pip install chunkshop` / `cargo install chunkshop-rs`. Not a service; not networked beyond DB clients.
- **Use case:** Batch ingest of text corpora into vector tables (PG/MariaDB/SQLite/ClickHouse).
- **Stage:** v0.4.0 — feature-complete for the documented scope. README badge still says "alpha"; functionality is closer to Beta/MVP.
- **Stakes:** Mid. No auth surface, no user data, but expensive ingest runs (embedding compute) that users would not want to lose silently.

This calibrates the audit: there's no health-check endpoint to lack, no rate-limiting story to miss, no on-call to wake. Operational readiness here means "fails loudly, leaves the table in a consistent state, prints what went wrong." Scalability means batch-throughput on million-chunk corpora, not request-per-second.

---

## Dimension 1: Functional Completeness

**Solid.** Every feature claimed in the README has runnable code paths and tests. The 16-cell cross-backend matrix integration test exists in both languages and pins parity at CI level.

### GAP-001 — Rust bakeoff is single-PG-only (NOTE)
- **Dimension:** Functional Completeness
- **Finding:** `chunkshop-rs bakeoff --config X.yaml` only supports a single `target:` block with the legacy `schema:` field. Python's `chunkshop bakeoff` supports `targets:` list across all 4 backends.
- **Evidence:** `rust/chunkshop/src/bakeoff/config.rs:25-29` — `BakeoffTargetConfig` has a single `schema_name` field.
- **What good looks like:** Multi-target Rust bakeoff that mirrors Python's `targets:` list and uses the `database:` field shape.
- **Status:** Documented as v0.4.1 follow-up #7 in `project_r2_followups.md` and in `docs/engines/*.md` "Gaps" sections. Not a blocker — Python bakeoff fills the gap.

### GAP-002 — Orchestrator is Python-only (NOTE)
- **Finding:** `chunkshop orchestrate` (parallel multi-cell subprocess fan-out) exists in Python only. Rust users running large multi-cell workloads must drive their own parallelism.
- **Evidence:** No equivalent in `rust/chunkshop/src/main.rs` — only `ingest` and `bakeoff` subcommands.
- **What good looks like:** Either (a) a Rust orchestrator, or (b) a documented "for orchestrate, use Python" note prominent in Rust README.
- **Status:** Acceptable v0.5+ scope; not a v0.4.0 gap.

---

## Dimension 2: Code Hygiene

**Strong.** No TODO/FIXME/HACK comments anywhere. The pre-existing `unused_imports` warning in extractor.rs was closed in this session.

### GAP-003 — Production-code `panic!` on `max_chars: 0` (MODERATE)
- **Dimension:** Code Hygiene
- **Finding:** `rust/chunkshop/src/chunker.rs:369` — `panic!("max_chars must be positive")` in `split_to_max_chars`. The function is called from chunker construction; pydantic / serde validation should prevent `max_chars: 0` from YAML, but a programmatic caller constructing a chunker with `max_chars: 0` would crash the process.
- **Evidence:**
  ```rust
  pub fn split_to_max_chars(text: &str, max_chars: usize) -> Vec<String> {
      if max_chars == 0 {
          panic!("max_chars must be positive");
      }
      ...
  }
  ```
- **What good looks like:** Either (a) return `Result<Vec<String>>` and propagate the error, or (b) change to `assert!(max_chars > 0, "...")` for explicit precondition documentation, or (c) accept the panic as defensive — library API misuse panic is idiomatic in Rust for invariant violations. Lowest-effort: keep, document with `# Panics` rustdoc block.

### GAP-004 — Python lib code uses `print()` (MODERATE)
- **Dimension:** Code Hygiene
- **Finding:** Six `print()` calls in non-CLI Python source files. They emit to stdout when chunkshop is used as a library, mixing with the host app's output.
- **Evidence:**
  - `src/chunkshop/runner.py:36` — per-line stdout of cell output
  - `src/chunkshop/orchestrator.py:68,81,96,135` — orchestration progress
  - `src/chunkshop/extractors/spacy_entities.py:26` — model-download notice
- **What good looks like:** Replace with `logging.info(...)` against module-level loggers. CLI entry points configure a stdout handler; library mode lets users wire their own. The `print(...)` in orchestrator is borderline OK because orchestrator IS a CLI feature, but the runner/spacy ones bleed into library usage.

### Hygiene wins worth noting
- 0 `TODO` / `FIXME` / `HACK` / `XXX` comments across both languages.
- 0 `println!` / `eprintln!` in Rust lib code (CLI uses `main.rs`).
- 0 bare `except:` clauses in Python.
- 1 `unsafe` block — `rust/chunkshop/src/backends/sqlite.rs:43` — for `sqlite3_auto_extension` registration, with proper `SAFETY:` comment and the documented sqlite-vec integration pattern. No further unsafe in the codebase.

---

## Dimension 3: Test Coverage & Quality

**Strong.** Test counts on the integration branch HEAD `460ff85`:

- **Rust:** 267 passed / 0 failed / 0 unexpected ignores
- **Python:** 349 passed / 0 failed / 11 skipped (DSN-conditional)

Tests cover failure modes, edge cases, cross-language parity, and cross-backend round-trips — not just happy paths.

### GAP-005 — Some integration tests silently skip without DSN env vars (MINOR)
- **Finding:** 11 Python tests + a small number of Rust tests skip when `CHUNKSHOP_TEST_DSN[_*]` is unset. The skip messages print but don't fail; a CI configuration with one missing DSN would mask coverage loss.
- **Evidence:** `pytest -q` reports `11 skipped`; Rust's `cargo test` reports skip messages on stderr via `eprintln!`.
- **What good looks like:** A CI gate that asserts skip-count == 0 when all DSNs are expected to be set, or a `--strict` mode that errors on skips.

### Test quality wins
- Cross-backend matrix tests (16 cells = 4 sources × 4 sinks) exist in both languages.
- Cross-language vector parity tests verify Python-written vectors are readable by Rust and vice versa.
- Real fastembed model used in tests (not mocked) — slower but exercises real ORT + tokenizer paths.
- Sample YAML smoke tests validate every shipped sample loads + ingests.

---

## Dimension 4: Scalability & Performance

**Good for the stated batch-ingest use case.** Calibrated for batch workloads, not OLTP serving.

### GAP-006 — No documented ingest ceiling at large scale (MINOR)
- **Finding:** chunkshop hasn't published measured numbers above ~1k chunks. Docs say SQLite is "single-machine workloads under ~1M chunks" but no measured throughput for 1M+ docs on PG/MariaDB/ClickHouse.
- **What good looks like:** A `docs/benchmarks-at-scale.md` with measured throughput on a representative corpus (1M+ docs, several model sizes).

### Scalability wins
- Per-document transactions → live progress queryable, mid-run crashes lose only the in-flight doc.
- Embedder threads + OMP/MKL/OPENBLAS thread caps tunable via YAML.
- Subprocess isolation in orchestrator → one cell's ORT memory doesn't fragment another's.
- ClickHouse path is genuinely append-only (cheap insert).

---

## Dimension 5: Security Posture

**Strong.** No hardcoded secrets, no shell injection, SQL injection well-defended.

### GAP-007 — `serde_yml` + `libyml` direct deps flagged unsound and unmaintained (CRITICAL)
- **Dimension:** Security Posture (also Dependency Health)
- **Finding:** `cargo audit` reports **RUSTSEC-2025-0067** (`libyml 0.0.5` unsound) and **RUSTSEC-2025-0068** (`serde_yml 0.0.12` unsound + unmaintained), both via chunkshop's **direct dependency** on `serde_yml`. `libyml::string::yaml_string_extend` is unsound (undefined behavior under specific inputs).
- **Evidence:** `rust/chunkshop/Cargo.toml:line ~serde_yml = "0.0.12"`; advisory at https://rustsec.org/advisories/RUSTSEC-2025-0068. Discovered via `cargo audit` run during this audit.
- **What good looks like:** Migrate to a maintained YAML parser. Options:
  - `serde_norway` — active fork of `serde_yaml`
  - `serde_yaml_ng` — another active fork
  - Roll back to `serde_yaml` (also unmaintained per RUSTSEC-2024-0320 but no unsound advisory)
- **Risk:** Parsing a malformed YAML could trigger UB. Adversarial-YAML risk is small (users author their own configs), but supply-chain risk is real — no future patches.

### GAP-008 — Transitive `rsa 0.9.10` Marvin Attack CVE (SERIOUS)
- **Dimension:** Security Posture (also Dependency Health)
- **Finding:** `cargo audit` reports **RUSTSEC-2023-0071** (severity 5.9 medium, "Marvin Attack: potential key recovery through timing sidechannels"). Pulled transitively via `sqlx-mysql 0.8.6` → `rsa 0.9.10`. **No fixed upgrade available** as of the audit date.
- **Evidence:** Cargo audit output; full dependency tree:
  ```
  rsa 0.9.10
  └── sqlx-mysql 0.8.6
      └── sqlx 0.8.6
          └── chunkshop-rs 0.4.0
  ```
- **What good looks like:** Vendor mitigation: prefer non-RSA auth on MariaDB connections (most MariaDB deployments use `mysql_native_password` or `caching_sha2_password`, not RSA). Document this in `docs/engines/mariadb.md`.
- **Risk:** Realistic only if (a) chunkshop's MariaDB client is exposed to a network position where an adversary can measure RSA-handshake timing, AND (b) the MariaDB auth uses an RSA-based plugin. Not a realistic threat for typical chunkshop deployments (local DB, trusted network), but worth a documented mitigation.

### GAP-009 — `paste 1.0.15` unmaintained (MODERATE)
- **Dimension:** Dependency Health
- **Finding:** **RUSTSEC-2024-0436** — `paste` crate no longer maintained. Transitive via `tokenizers`, `fastembed`, `clickhouse`, `image`.
- **What good looks like:** Wait for upstream (`tokenizers`, `fastembed`) to migrate; not a chunkshop-fixable item. Track.

### Security wins (well-defended)

| Pattern | Defense | Evidence |
|---|---|---|
| SQL injection via identifiers | Regex allowlist `^[a-z_][a-z0-9_]*$` enforced at config-load | `config.rs:999` + `config.py:546` |
| SQL injection via values | Parameter binding (psycopg `%s`, sqlx `?`, clickhouse parametrized) | sampled across `sinks/*.{rs,py}` |
| jsonb-path injection in `promote_metadata` | Regex per dot-separated segment | `config.py:376` + `config.rs:57` |
| Shell injection in orchestrator | `subprocess.Popen(argv_list, shell=False)` | `orchestrator.py:39` |
| Hardcoded credentials | None — all DSNs via env vars at runtime | grep verified |
| Pickle / eval / exec | None used | grep verified |
| YAML unsafe-load | Python uses `yaml.safe_load`; Rust uses `serde_yml` (CRITICAL above) | sampled |
| Pydantic `extra="forbid"` | Typos in YAML rejected at load, not silently ignored | every model |

---

## Dimension 6: Operational Readiness

**Solid for a batch CLI tool.** Different shape than "service" operational readiness.

### GAP-010 — No structured logging (MINOR)
- **Dimension:** Operational Readiness
- **Finding:** Both languages emit human-readable log lines. Aggregating runs across many orchestrator-spawned cells requires regex / awk parsing.
- **Evidence:** Rust uses `tracing` with default formatter; Python uses `logging` with default formatter plus six `print()` calls.
- **What good looks like:** Optional JSON log output via env var or YAML config — for users running chunkshop in a job-runner that wants structured ingest into a log aggregator.

### GAP-011 — No retry on transient DB connection errors (MINOR)
- **Finding:** Transient network blips during ingest fail the cell. Restart upserts cleanly (primary key `{doc_id}::{seq_num}` makes it idempotent), but the cell exits with non-zero.
- **What good looks like:** Configurable retry-with-backoff on connection-level errors (not on logical errors).
- **Workaround:** Already idempotent — rerun fixes it. Trade-off: complicating sink code for a workflow that's already-correct on retry isn't obviously worth it.

### Ops wins
- Heartbeat logs every N docs (default 25) during long ingests.
- Per-doc commit semantics → live progress via `SELECT COUNT(DISTINCT doc_id)`.
- Non-zero exit code on failure.
- Orchestrator emits start/finish lines per cell with PID, wall time, exit code.
- Error messages name the offending env var (e.g., `DSN env var CHUNKSHOP_DSN not set`).

---

## Dimension 7: Configuration & Environment Management

**Strong.** YAML-driven, schema-enforced, env-var-resolved.

### GAP-012 — `chunkshop validate <yaml>` dry-run command missing (MINOR)
- **Dimension:** Configuration & Environment Management
- **Finding:** Validating a YAML requires running `chunkshop ingest` (which actually opens DB connections and creates tables). A pure config-validation pass would catch typos before any side effects.
- **What good looks like:** `chunkshop validate --config x.yaml` — load + parse + run pydantic / serde validation, exit 0/1 without touching DBs.
- **Effort:** small — pydantic + serde already do the validation; just need a CLI plumbing.

### GAP-013 — `chunkshop init` scaffolding command missing (MINOR)
- **Finding:** New users write the YAML by hand or copy from `docs/samples/`. A guided `chunkshop init` could prompt for backend + corpus path + emit a minimal cell YAML.
- **What good looks like:** `chunkshop init --backend postgres --corpus ./docs/*.md` produces a `cell.yaml` ready to `ingest`.
- **Status:** Nice-to-have; sample YAMLs in `docs/samples/` already cover the gap.

### Config wins
- Pydantic models with `extra="forbid"` — every typo errors at load.
- Identifier regex validation per field.
- DSN-via-env-var — same YAML travels across environments.
- Discriminated unions on source/sink/chunker/embedder/extractor → typo on `type:` errors at load.

---

## Dimension 8: Dependency Health

Three Rust advisories captured above (CRITICAL `serde_yml`, SERIOUS `rsa`, MODERATE `paste`). Python `pip-audit`: **no known vulnerabilities**.

### Dependency posture
- Both Python and Rust use lockfiles (`uv.lock` tracked; `Cargo.lock` gitignored per monorepo convention).
- Direct deps pinned to minor versions; transitive deps managed by the lockfile.
- License classifiers consistent (MIT across the board).
- No abandoned direct deps except `serde_yml` (covered in GAP-007).

---

## Dimension 9: Data Integrity & Resilience

**Strong for batch ingest.**

### GAP-014 — No documented v0.3 → v0.4 migration story (MODERATE)
- **Dimension:** Data Integrity & Resilience
- **Finding:** A user with a populated v0.3.x table on Postgres → upgrading to v0.4.0 has no documented "how to migrate" path. The schema is structurally compatible (v0.4 added per-engine sinks, didn't change the PG table shape), but this isn't explicit in any release-notes or upgrade-guide.
- **What good looks like:** `docs/upgrading.md` or release-notes entry confirming "v0.3.x tables are readable by v0.4.0 without migration; the only schema-relevant change is X" (or "no change, just re-run `mode: append` to add more rows").

### GAP-015 — ClickHouse default to `MergeTree` lets duplicates accumulate on re-ingest (NOTE)
- **Finding:** A v0.4.0 user running `mode: append` twice on the same CH cell produces duplicate rows by design. Documented in `docs/engines/clickhouse.md`. Could surprise a user who expects upsert-style semantics.
- **What good looks like:** Either (a) default `engine` to `ReplacingMergeTree(created_at) ORDER BY (id)`, or (b) emit a warning the first time a CH cell is used in append mode without ReplacingMergeTree.

### Data integrity wins
- Append pre-flight (dim check, table exists, source_tag required) before any INSERT.
- Foreign-tag refusal on overwrite mode unless `force_overwrite: true`.
- `source` column write-once on UPSERT collisions.
- `{doc_id}::{seq_num}` primary key → reruns idempotent on PG/MariaDB/SQLite.
- Cross-language vector parity verified per backend.

---

## Dimension 10: Installation, Setup & Usability

**Good docs, one install footgun.**

### GAP-016 — Default `uv sync` doesn't install backend extras (SERIOUS)
- **Dimension:** Installation, Setup & Usability
- **Finding:** Running `cd python && uv sync --extra dev` (the README's quoted step) installs chunkshop without `pymysql`, `clickhouse-connect`, or `sqlite-vec`. A user who picks MariaDB / SQLite / ClickHouse as their backend will get an `ImportError` at first ingest. Discovered during the v0.4.0 RT validation in this session — even our own worktree venv had to be patched.
- **Evidence:**
  ```
  cd python && uv sync --extra dev    # README step
  python -c "import chunkshop"        # works
  chunkshop ingest --config mariadb-cell.yaml
  # ModuleNotFoundError: No module named 'pymysql'
  ```
- **What good looks like:** Three options:
  1. Update the README install step to `uv sync --extra dev --extra all-backends` (LOWEST-EFFORT FIX).
  2. Reshape extras so the base install includes `pymysql`, `clickhouse-connect`, `sqlite-vec` (heavier base install but no footgun).
  3. Lazy-import per-backend and emit a clear "install the `chunkshop[mariadb]` extra to use this backend" error.
- **Risk if skipped:** Every first-time user picking a non-PG backend hits this within minutes of install.

### GAP-017 — `Development Status :: 3 - Alpha` classifier vs v0.4.0 feature set (MINOR)
- **Finding:** `pyproject.toml` classifier still says `3 - Alpha`. By the standard PyPI maturity stages (3=Alpha, 4=Beta, 5=Production/Stable), chunkshop v0.4.0 is closer to Beta — cross-tested, documented, multi-backend, dual-language.
- **What good looks like:** Update to `Development Status :: 4 - Beta` to set user expectations correctly.

### Install/setup wins
- Published on PyPI and crates.io.
- Per-engine docs landed in this session.
- 4 sample YAMLs per backend, plus tutorial YAMLs.
- Cross-engine mix doc.
- Bakeoff walkthrough.
- Both CLIs return `--version`.
- Sample bakeoff configs (`docs/samples/bakeoff-{ntsb,sales-crm,scotus,v04}/`).

---

## Summary dashboard

### Findings by severity

| Severity | Count |
|---|---:|
| BLOCKER | 0 |
| CRITICAL | 1 |
| SERIOUS | 2 |
| MODERATE | 4 |
| MINOR | 7 |
| NOTE | 3 |
| **Total** | **17** |

### Findings by dimension

| Dimension | BLOCKER | CRITICAL | SERIOUS | MODERATE | MINOR | NOTE |
|---|---:|---:|---:|---:|---:|---:|
| 1. Functional Completeness | 0 | 0 | 0 | 0 | 0 | 2 |
| 2. Code Hygiene | 0 | 0 | 0 | 2 | 0 | 0 |
| 3. Test Coverage | 0 | 0 | 0 | 0 | 1 | 0 |
| 4. Scalability & Performance | 0 | 0 | 0 | 0 | 1 | 0 |
| 5. Security Posture | 0 | 1 | 1 | 0 | 0 | 0 |
| 6. Operational Readiness | 0 | 0 | 0 | 0 | 2 | 0 |
| 7. Configuration & Env | 0 | 0 | 0 | 0 | 2 | 0 |
| 8. Dependency Health | 0 | 0 | 0 | 1 | 0 | 0 |
| 9. Data Integrity | 0 | 0 | 0 | 1 | 0 | 1 |
| 10. Installation & Usability | 0 | 0 | 1 | 0 | 1 | 0 |

### Verdict: **READY WITH CAVEATS**

chunkshop v0.4.0 ships and works. Tagged and pushed. The single CRITICAL (`serde_yml` direct dep is unmaintained + unsound per RUSTSEC) and the SERIOUS install-extras footgun should be fixed in a fast-follow v0.4.1. Everything else is normal v0.4.x → v0.5 tracked debt.

**For the imminent question "can users run this on their corpora right now":** yes, with the docs as-is. Postgres users hit no install footgun. MariaDB / SQLite / ClickHouse users following the README's `uv sync --extra dev` will hit GAP-016 — patching this is a one-line README fix.

**For the next-release question "what must happen before tagging v0.4.1":** GAP-007 (migrate off `serde_yml`). Everything else is fast-follow.

See `ProdReady-RiskAssessment.md` for what each SERIOUS+ finding looks like when it goes wrong, and `ProdReady-FixPlan.md` for the prioritized remediation list.
