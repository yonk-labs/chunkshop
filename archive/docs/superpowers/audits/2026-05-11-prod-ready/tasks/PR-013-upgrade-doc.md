# PR-013 — Document v0.3 → v0.4 upgrade path

**Priority:** P3
**Effort:** XS (~30 min)
**Dependencies:** none
**GAP-IDs:** GAP-014

## Problem

A v0.3.x user on Postgres upgrading to v0.4.0 has no documentation answering: "does my existing chunks table need migration?" The answer is "no — the schema is structurally identical; v0.4 added per-engine sinks without changing the PG layout" — but that needs to be in writing.

## Solution

Create `docs/upgrading.md` with version-pair sections.

### Sketch

```markdown
# Upgrading chunkshop

## v0.3.x → v0.4.0

**TL;DR:** No schema migration required. Existing v0.3.x Postgres tables work
as-is with v0.4.0.

### What changed
- Three new backends added (MariaDB, SQLite, ClickHouse) — strictly additive,
  no change to Postgres path.
- Trait surface refactored internally (Backend / Sink / Source split).
  Public Python and Rust APIs unchanged.
- Cross-language matrix tests added — no impact on existing code.

### What stays the same
- PG table layout: identical.
- YAML schema: identical (the v0.3.x `target.schema:` field and v0.4.x
  `target.database:` for PG are aliased; both work).
- Embedder model defaults: unchanged.
- CLI command surface: unchanged (plus the new `bakeoff` Rust binary).

### Action required
None. Re-run your existing cells with `chunkshop 0.4.0` installed — they
work identically.

### If you want to try the new backends
See [`docs/engines/`](engines/) and [`docs/mixing-sources-and-sinks.md`](mixing-sources-and-sinks.md).
```

(Extend with `v0.2 → v0.3` section if relevant; check changelog/git history for prior breaking changes.)

## Acceptance Criteria

- [ ] `docs/upgrading.md` exists.
- [ ] README links to it from the "Documentation" section.
- [ ] At least one v0.3 → v0.4 pair documented with clear "what changed, what stays the same, action required."

## Risk if Skipped

Users opening issues asking the same migration question. Friction; not a real gap.
