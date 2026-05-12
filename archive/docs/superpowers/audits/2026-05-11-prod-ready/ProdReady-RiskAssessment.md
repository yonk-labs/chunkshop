# Production Readiness Audit — Risk Assessment

## TL;DR
Three SERIOUS+ findings carry real production risk worth tracking. **GAP-007** (`serde_yml` unsound dependency) is silent + unrecoverable in worst-case, but practically low-likelihood for typical YAML inputs. **GAP-008** (transitive `rsa` Marvin Attack CVE) is contained to adversarial-network MariaDB deployments. **GAP-016** (default install lacks backend extras) is the only finding where the first user trying a non-PG backend hits a hard failure. None are deployment blockers; all are tractable in v0.4.1.

---

## Risk-scoring legend

- **Likelihood:** CERTAIN (first use) / LIKELY (first week) / POSSIBLE (realistic conditions) / UNLIKELY (adversarial/unusual)
- **Blast radius:** TOTAL (all users) / MAJOR (many users) / CONTAINED (segment) / MINIMAL (edge case)
- **Detection:** SILENT (no signals) / DELAYED (eventual) / VISIBLE (user-facing) / LOUD (crash/page)
- **Recovery:** UNRECOVERABLE (data loss) / HARD (manual intervention) / MODERATE (deploy/config) / EASY (restart/retry)

---

## CRITICAL findings

### GAP-007 — `serde_yml` / `libyml` unsound, unmaintained (CRITICAL)

**Risk scenario:** A maliciously-crafted YAML config (or a future YAML edge case) triggers undefined behavior in `libyml::string::yaml_string_extend`. In the worst case, this is a memory-safety violation in chunkshop's config parser. More realistically: a future fastembed model name with unusual UTF-8 sequences triggers UB during config load; symptoms could range from "garbled error message" to "process crash mid-ingest" to silent corruption of in-memory config state.

A separate but related risk: `serde_yml` and `libyml` are unmaintained. **No future security advisories will be patched.** Whatever issues exist today, persist forever.

| Axis | Rating | Reasoning |
|---|---|---|
| Likelihood | POSSIBLE | Triggering the documented unsoundness requires specific input shapes; no public exploit. Supply-chain "no future patches" is CERTAIN. |
| Blast radius | TOTAL | Affects every Rust user — `serde_yml` parses every chunkshop YAML config. |
| Detection | SILENT | UB might not be observable. Process crash is LOUD; silent memory corruption is the worst case. |
| Recovery | MODERATE | A migration to `serde_yaml_ng` or `serde_norway` is a contained code change (one direct dep swap; the `serde` derives are unchanged). |

**Mitigation:** Migrate to `serde_yaml_ng` (or `serde_norway`) in v0.4.1. Both are active forks of `serde_yaml` with the same `serde::Deserialize` derive contract. Estimated effort: small — single `Cargo.toml` change, run tests, verify YAML parsing semantics unchanged.

**Why it's CRITICAL not BLOCKER:** v0.4.0 is already tagged and shipped. The risk has been latent through every prior chunkshop release that used `serde_yml`. There's no evidence of in-the-wild exploitation. But this *must* be the first thing fixed in v0.4.1.

---

## SERIOUS findings

### GAP-008 — `rsa 0.9.10` Marvin Attack CVE (SERIOUS)

**Risk scenario:** An adversary positioned on the network path between chunkshop and a MariaDB server (with RSA-based MariaDB auth — `caching_sha2_password` or `sha256_password` plugins) measures the timing of multiple TLS / auth handshakes. With sufficient samples, they recover bits of the RSA private key. Realistic only if (a) chunkshop's MariaDB connection traverses untrusted network, AND (b) MariaDB is configured to use an RSA-based auth plugin, AND (c) the adversary can observe many handshakes.

| Axis | Rating | Reasoning |
|---|---|---|
| Likelihood | UNLIKELY | Most chunkshop deployments connect to a local or VPC-internal MariaDB on `mysql_native_password`, not RSA-based plugins. The Marvin Attack requires repeated handshake observation. |
| Blast radius | CONTAINED | Affects only chunkshop deployments using MariaDB + RSA auth + adversarial network. Single backend, narrow auth configuration. |
| Detection | SILENT | No application-level signal. The "attack succeeded" event is invisible to chunkshop. |
| Recovery | MODERATE | Switch MariaDB auth to a non-RSA plugin, rotate credentials. Cannot fix in chunkshop code (no upstream patch on `rsa`). |

**Mitigation:**
1. Document in `docs/engines/mariadb.md`: "If you connect to MariaDB across untrusted network, prefer `mysql_native_password` auth or use TLS termination outside chunkshop." (LOW EFFORT)
2. Track the `rsa` crate for an eventual fix.
3. Consider switching to a different MariaDB driver if `sqlx-mysql` doesn't move off `rsa`.

**Why SERIOUS not CRITICAL:** the attack precondition (adversarial network position + RSA auth + many handshakes) doesn't match any plausible chunkshop deployment topology. Most chunkshop users run a local DB or a managed cloud DB with tightly-scoped network access. But for the user who DOES expose chunkshop's MariaDB connection across untrusted network with RSA auth, this is a real risk and chunkshop's docs should call it out.

### GAP-016 — Default install lacks backend extras (SERIOUS)

**Risk scenario:** A user installs chunkshop following the README quickstart (`uv sync --extra dev` or `pip install chunkshop`). They configure a MariaDB / SQLite / ClickHouse cell. They run `chunkshop ingest --config x.yaml`. They see:

```
ModuleNotFoundError: No module named 'pymysql'
```

(or `clickhouse_connect` or `sqlite_vec`.) They have to figure out which extra to install. They might wrongly conclude chunkshop doesn't support their backend, or they file an issue, or they give up.

| Axis | Rating | Reasoning |
|---|---|---|
| Likelihood | CERTAIN | Every first-time user picking a non-PG backend hits this. Confirmed: this session's RT validation hit it on a freshly-bootstrapped worktree. |
| Blast radius | MAJOR | All non-PG-first users (probably ~60% of installs given PG's dominance, but still ~40%). |
| Detection | VISIBLE | The traceback names the missing module. Users can probably reason out the fix but it's a friction point and an "abandons during eval" risk. |
| Recovery | EASY | `uv sync --extra all-backends` or `pip install 'chunkshop[all-backends]'`. |

**Mitigation:** Three options ranked by effort/reward:

1. **README update only (XS effort, full coverage)** — change the install step in README to `uv sync --extra dev --extra all-backends`. Solves the surface case for ~all new users.
2. **Lazy-import + good error (S effort)** — in each `chunkshop.backends.{mariadb,sqlite,clickhouse}` module, wrap the driver import in a try/except that raises a chunkshop-branded `ImportError` like: `"MariaDB backend requires the 'mariadb' extra. Install with: pip install 'chunkshop[mariadb]'"`. Way better DX than the raw `ModuleNotFoundError`.
3. **Pull driver libs into base install (M effort)** — make `pymysql`, `clickhouse-connect`, `sqlite-vec` direct (non-optional) deps. Heavier base install (~12 MB extra) but zero-config for end users.

Suggest **(1) + (2)** combined: README fix is instant; lazy-import + branded error is a small follow-up.

**Why SERIOUS not CRITICAL:** non-PG users get a visible, recoverable error. Postgres users (the default-recommendation path) don't see it at all. Real friction but not a deployment blocker.

---

## Risk matrix

```
                          LIKELIHOOD →
                  UNLIKELY    POSSIBLE     LIKELY      CERTAIN
                ╔════════════╦═══════════╦═══════════╦═══════════╗
       TOTAL    ║            ║  GAP-007  ║           ║           ║
                ║            ║ serde_yml ║           ║           ║
                ╠════════════╬═══════════╬═══════════╬═══════════╣
       MAJOR    ║            ║           ║           ║  GAP-016  ║
                ║            ║           ║           ║  default  ║
                ║            ║           ║           ║  install  ║
                ╠════════════╬═══════════╬═══════════╬═══════════╣
       CONTAINED║  GAP-008   ║           ║           ║           ║
                ║  rsa CVE   ║           ║           ║           ║
                ║  (MariaDB) ║           ║           ║           ║
BLAST RADIUS ↓  ╠════════════╬═══════════╬═══════════╬═══════════╣
       MINIMAL  ║            ║           ║           ║           ║
                ║            ║           ║           ║           ║
                ╚════════════╩═══════════╩═══════════╩═══════════╝
```

---

## What keeps you up at night

If chunkshop v0.4.0 is in active production use across many users, the top 3 scenarios worth losing sleep over:

### #1 — Day-one users on non-PG backends hit `ImportError` and bounce

**Why:** GAP-016 is CERTAIN × MAJOR. Every install funnel for a non-PG user starts with a confusing error. Even with good docs, the first 60 seconds of evaluation determine whether a user proceeds. Fixable in a 1-line README PR plus a small lazy-import follow-up.

**Defense in depth:**
- README step says `uv sync --extra dev --extra all-backends` explicitly.
- Each per-backend module emits a chunkshop-branded error pointing at the right extra.
- The first paragraph of `docs/engines/mariadb.md` (and the other non-PG engines) leads with the install command rather than the DSN.

### #2 — A future `libyml` UB lands at a customer site that doesn't pin chunkshop's version

**Why:** GAP-007 is POSSIBLE × TOTAL. Today the unsoundness is latent — no known trigger. Tomorrow someone files a Rust issue with a YAML repro that crashes their ingest pipeline. We can't patch the dep — it's unmaintained. The fix is to migrate before that happens.

**Defense in depth:**
- Schedule the migration in v0.4.1.
- In the interim, lock chunkshop's Rust binary to a specific `serde_yml` patch version to reduce surprise on `cargo update`.
- Document YAML parser provenance in `docs/architecture.md` so users with their own threat models can decide.

### #3 — A MariaDB user across an adversarial network has RSA-based auth and is profiled by a sophisticated attacker

**Why:** GAP-008 is UNLIKELY × CONTAINED but UNRECOVERABLE if it lands. The attack precondition is narrow, but the consequence (private key recovery) is catastrophic for that user.

**Defense in depth:**
- Document the attack precondition prominently in `docs/engines/mariadb.md`.
- Recommend `mysql_native_password` or TLS-terminated connections for adversarial-network deployments.
- Watch the `rsa` crate for a Marvin-resistant release; bump as soon as one ships.

---

## What does NOT keep you up at night

These were inspected and found solid:

- **SQL injection.** Identifier regex `^[a-z_][a-z0-9_]*$` + parameterized values across all 4 sinks. Multiple code-path samples confirmed no value-interpolation-as-SQL pattern.
- **Hardcoded credentials.** None found — every DSN is resolved from an env var at runtime.
- **Shell injection.** Orchestrator subprocess uses `argv` list with `shell=False`. The only subprocess invocation in either codebase.
- **YAML unsafe-load.** Python uses `yaml.safe_load` (sampled). Rust unsoundness is real (GAP-007) but separate from the unsafe-loader-execution pattern.
- **Eval / exec / pickle.** Zero matches in both codebases.
- **Tests passing on green.** 267 Rust / 349 Python with 0 failures.
- **Mid-run crash recovery.** Per-doc commit semantics + idempotent reruns.
- **Foreign source_tag accidents.** Refused by default on `mode: overwrite`; requires explicit `force_overwrite: true`.

---

## Calibration note

This is a CLI + library batch tool, not a deployed service. Risks that would otherwise dominate a prod-readiness audit — auth bypass, rate-limit bypass, request-injection, IDOR — don't apply because chunkshop has no request surface. The risk surface is YAML parsing, DB connection paths, and embedder model handling. All three were inspected.

Continue to the Fix Plan (`ProdReady-FixPlan.md`) for the prioritized remediation roadmap.
