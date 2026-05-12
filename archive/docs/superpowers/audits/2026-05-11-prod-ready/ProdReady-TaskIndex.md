# Production Readiness Audit — Task Index

## TL;DR
18 tasks across P0–P4 tiers. **Minimum Viable Ship List = 1 task (PR-001).** **Sleep Well List = 3 tasks, ~1 day total.** Full polish across all 18 = ~3.5 weeks. The two issues with day-one user impact (CRITICAL `serde_yml` migration + SERIOUS default-install footgun) are PR-001, PR-002, PR-003 — fixable in well under a day.

---

## Master index

| ID | Title | Priority | Effort | Dependencies | Status |
|----|-------|----------|--------|--------------|--------|
| [PR-001](tasks/PR-001-migrate-serde-yml.md) | Migrate off `serde_yml` to a maintained YAML parser | P0 | S | — | TODO |
| [PR-002](tasks/PR-002-readme-all-backends.md) | README install step includes `--extra all-backends` | P1 | XS | — | TODO |
| [PR-003](tasks/PR-003-branded-import-errors.md) | Branded lazy-import errors for backend drivers | P1 | S | — | TODO |
| [PR-004](tasks/PR-004-document-yaml-parser.md) | Document YAML parser provenance in architecture + release notes | P2 | XS | PR-001 | TODO |
| [PR-005](tasks/PR-005-no-prod-panic.md) | Eliminate production-code `panic!` on `max_chars: 0` | P2 | S | — | TODO |
| [PR-006](tasks/PR-006-python-logging.md) | Replace Python lib `print()` with module-level `logging` | P2 | S | — | TODO |
| [PR-007](tasks/PR-007-clickhouse-replacing-default.md) | Warn on CH `mode: append` without ReplacingMergeTree | P2 | S | — | TODO |
| [PR-008](tasks/PR-008-validate-command.md) | `chunkshop validate <yaml>` dry-run CLI | P2 | M | — | TODO |
| [PR-009](tasks/PR-009-mariadb-rsa-doc.md) | Document Marvin Attack mitigation in MariaDB engine doc | P3 | XS | — | TODO |
| [PR-010](tasks/PR-010-init-scaffold.md) | `chunkshop init` scaffolding command | P3 | M | — | TODO |
| [PR-011](tasks/PR-011-classifier-beta.md) | Bump Python classifier "3 - Alpha" → "4 - Beta" | P3 | XS | — | TODO |
| [PR-012](tasks/PR-012-strict-test-mode.md) | `--strict` test mode that fails on unexpected skips | P3 | S | CI access | TODO |
| [PR-013](tasks/PR-013-upgrade-doc.md) | Document v0.3 → v0.4 upgrade path | P3 | XS | — | TODO |
| [PR-014](tasks/PR-014-json-logging.md) | Optional structured (JSON) logging | P3 | M | PR-006 | TODO |
| [PR-015](tasks/PR-015-scale-benchmarks.md) | Publish at-scale benchmarks | P3 | L | — | TODO |
| [PR-016](tasks/PR-016-retry-transient.md) | Retry-with-backoff on transient connection errors | P4 | M | — | TODO |
| [PR-017](tasks/PR-017-rust-orchestrator.md) | Rust orchestrator (multi-cell subprocess fan-out) | P4 | XL | — | TODO |
| [PR-018](tasks/PR-018-multi-target-rust-bakeoff.md) | Multi-target Rust bakeoff (cross-backend comparison) | P4 | M | — | TODO |

---

## Minimum Viable Ship List (P0)

If you can only do one thing before tagging v0.4.1:

- **PR-001** — Migrate off `serde_yml`.

**Effort:** ~half day. Closes the CRITICAL dep-audit finding.

---

## Sleep Well List (P0 + P1)

Recommended pre-v0.4.1 batch:

- **PR-001** — Migrate off `serde_yml` (S)
- **PR-002** — README install includes `all-backends` (XS)
- **PR-003** — Branded lazy-import errors per backend (S)

**Effort:** ~1 day total. Closes the CRITICAL + the only SERIOUS issue with day-one user impact.

---

## Effort by tier

| Tier | Tasks | Total effort |
|---|---:|---|
| P0 | 1 | ~half day |
| P1 | 2 | ~half day |
| P2 | 5 | ~2 days |
| P3 | 7 | ~1 week |
| P4 | 3 | ~2 weeks |
| **All** | **18** | **~3.5 weeks** |

---

## See also

- [`ProdReady-GapAnalysis.md`](ProdReady-GapAnalysis.md) — Findings by dimension, with evidence.
- [`ProdReady-RiskAssessment.md`](ProdReady-RiskAssessment.md) — SERIOUS+ findings with likelihood × blast-radius scoring.
- [`ProdReady-FixPlan.md`](ProdReady-FixPlan.md) — Same tasks grouped by priority tier with acceptance criteria narratives.
