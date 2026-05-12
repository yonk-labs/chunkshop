# PR-004 — Document YAML parser provenance in architecture doc + release notes

**Priority:** P2
**Effort:** XS
**Dependencies:** PR-001
**GAP-IDs:** GAP-007 (followup)

## Problem

After PR-001 swaps `serde_yml` for `serde_yaml_ng`, users with their own audit pipelines (or security teams running their own `cargo audit`) need to know:

1. The YAML parser dependency changed.
2. The replacement is actively maintained.
3. The migration was driven by RUSTSEC-2025-0067/0068, not by a chunkshop-side YAML semantics change.

## Solution

Two doc edits:

### 1. `docs/architecture.md`

In the "Cross-language parity" section, add a line under the embedder table:

```markdown
- **YAML parser**: `pyyaml.safe_load` on Python; `serde_yaml_ng` on Rust
  (maintained fork of `serde_yaml`). Migration from `serde_yml` in v0.4.1
  was supply-chain hygiene, not a behavior change — YAML loading semantics
  unchanged.
```

### 2. v0.4.1 tag message / release-notes

Include a CHANGELOG-style entry:

```
Security: migrated YAML parser from serde_yml (unmaintained, RUSTSEC-2025-0067
+ RUSTSEC-2025-0068) to serde_yaml_ng (maintained). No behavior change for
end users — all YAML samples and tests pass identically. cargo audit now
reports zero advisories on direct dependencies.
```

## Acceptance Criteria

- [ ] `docs/architecture.md` mentions `serde_yaml_ng` as the Rust YAML parser.
- [ ] v0.4.1 tag message includes the migration note.
- [ ] An audit consumer running `cargo audit` against v0.4.1 sees a clean direct-dep advisory list.

## Risk if Skipped

Users with their own audit pipelines hit a "dep changed" event in their tooling without context. They open an issue asking "why?" — costing time on both sides.
