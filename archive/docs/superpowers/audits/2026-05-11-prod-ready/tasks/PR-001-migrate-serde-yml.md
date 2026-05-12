# PR-001 — Migrate off `serde_yml` to a maintained YAML parser

**Priority:** P0
**Effort:** S (~half day)
**Dependencies:** none
**GAP-IDs:** GAP-007

## Problem

chunkshop's Rust port depends on `serde_yml = "0.0.12"` for YAML config parsing. `cargo audit` reports two RUSTSEC advisories against this dependency chain:

- **RUSTSEC-2025-0067** — `libyml::string::yaml_string_extend` is unsound and unmaintained.
- **RUSTSEC-2025-0068** — `serde_yml` crate is unsound and unmaintained.

Both flag unsoundness (potential undefined behavior under specific inputs) plus the "no future patches" supply-chain risk.

**Evidence:**
```
$ cargo audit
Crate:     libyml
Version:   0.0.5
Warning:   unsound
Title:     `libyml::string::yaml_string_extend` is unsound and unmaintained
ID:        RUSTSEC-2025-0067

Crate:     serde_yml
Version:   0.0.12
Warning:   unsound
Title:     serde_yml crate is unsound and unmaintained
ID:        RUSTSEC-2025-0068
```

`serde_yml` is a direct chunkshop dependency:
```toml
# rust/chunkshop/Cargo.toml
serde_yml = "0.0.12"
```

## Solution

Replace `serde_yml` with one of the maintained `serde_yaml` forks:

- **`serde_yaml_ng`** — `https://crates.io/crates/serde_yaml_ng` — direct fork of `serde_yaml` by an active maintainer. **Recommended.**
- **`serde_norway`** — `https://crates.io/crates/serde_norway` — alternative active fork.

Both expose the same `serde::Deserialize` derive contract chunkshop uses, so the migration is a dep swap + import rename.

### Steps

1. **Swap the dep:**
   ```toml
   # rust/chunkshop/Cargo.toml
   -serde_yml = "0.0.12"
   +serde_yaml_ng = "0.10"
   ```

2. **Update imports.** Grep for `serde_yml::` and replace with `serde_yaml_ng::`:
   ```bash
   grep -rln 'serde_yml' rust/chunkshop/src rust/chunkshop/tests | \
     xargs sed -i 's/serde_yml::/serde_yaml_ng::/g; s/serde_yml /serde_yaml_ng /g'
   ```
   Manually verify any `use serde_yml::*` lines also got rewritten.

3. **Run tests:**
   ```bash
   cd rust && cargo test -p chunkshop-rs
   ```
   Expect 267 passing / 0 failed (the post-v0.4.0 baseline).

4. **Run cargo audit:**
   ```bash
   cargo audit
   ```
   Expect RUSTSEC-2025-0067 and RUSTSEC-2025-0068 gone. Other warnings (`rsa`, `paste`) are transitive and unrelated to this PR.

5. **Smoke-test sample YAMLs:**
   ```bash
   for yaml in docs/samples/sample*.yaml; do
     echo "=== $yaml ==="
     cargo run -q -p chunkshop-rs -- ingest --config "$yaml" 2>&1 | tail -3
   done
   ```
   Every sample loads + ingests cleanly. (Some require DSN env vars; skip those that can't run locally.)

6. **Update Rust release notes / changelog** to mention the migration. Suggest one line in `docs/architecture.md` under "Cross-language parity": "YAML parser: `serde_yaml_ng` on Rust (maintained fork of `serde_yaml`)."

## Acceptance Criteria

- [ ] `cargo audit` reports zero `serde_yml` / `libyml` advisories.
- [ ] All 267 Rust tests pass.
- [ ] All shipped sample YAMLs load and ingest end-to-end (the ones reachable from the dev box's DSN set).
- [ ] Cross-language parity tests (`tests/dialect_*_parity.rs`, `tests/cross_language_*.rs`) unchanged.
- [ ] No new transitive deps that themselves carry advisories.

## Risk if Skipped

`serde_yml`'s unsoundness is latent today — no known YAML input triggers it. Tomorrow someone files a Rust issue with a YAML repro that crashes chunkshop's ingest pipeline. We can't patch the dep; it's unmaintained. The risk compounds with every release that ships this dep.

For users with security-conscious procurement workflows, having a CRITICAL-rated direct dep CVE-equivalent in chunkshop's bill of materials is a non-starter.

## Notes

- The migration is mechanical because `serde_yml` was itself a fork of `serde_yaml` with the same API surface. The newer forks preserve that API.
- Test thoroughly with edge-case YAMLs (unicode, deeply nested, malformed, very long strings). The unsoundness was in `libyml`'s string handling — a sample input that would have triggered it may have already been written into chunkshop's test corpus as a regression net for the migration.
