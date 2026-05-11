# Upgrading chunkshop

How to move between chunkshop versions. Backward-compatibility is the
default; breaking changes appear in semver-major bumps and are called out
here ahead of time.

## v0.3.x → v0.4.0

**TL;DR:** No schema migration required. Existing v0.3.x Postgres tables
work as-is with v0.4.0.

### What changed
- **Three new backends added.** MariaDB 11.7+, SQLite + sqlite-vec, and
  ClickHouse 24.10+ are now first-class sinks. Strictly additive — no
  change to the Postgres path.
- **Trait surface refactored internally.** R1 split `Backend` (dialect +
  connection) from `Sink` (chunkshop's data-model semantics). Python `Protocol`
  classes mirror this on the Python side. Public APIs (`Pipeline`,
  `run_cell`, `chunkshop ingest`) unchanged.
- **Cross-language matrix tests added.** Every DB-source × DB-sink combo
  round-trips in both languages. Pinned in CI.
- **CLI additions:** `chunkshop validate` (dry-run config check),
  `chunkshop init` (interactive scaffolding). `chunkshop ingest`,
  `orchestrate`, `bakeoff` unchanged.

### What stays the same
- **PG table layout:** identical. Same columns, same types, same indices.
- **YAML schema:** identical. The v0.3.x `target.schema:` field and v0.4.x
  `target.database:` for PG are aliased; both work. Pydantic / serde
  warnings on legacy fields are unchanged.
- **Embedder model defaults:** unchanged (`Xenova/bge-base-en-v1.5-int8`).
- **CLI command surface:** unchanged for existing commands.
- **Cross-language vector parity:** vectors written by v0.3.x are readable
  by v0.4.0 and vice versa.

### Action required
**None for Postgres users.** Re-run your existing cells with chunkshop
0.4.0 installed — they work identically.

**For Python users on non-PG backends:** the v0.4.0 `python/pyproject.toml`
moved per-backend driver libraries into optional extras. If you previously
installed chunkshop and want to use a new non-PG backend:

```bash
pip install 'chunkshop[mariadb]'      # adds pymysql
pip install 'chunkshop[sqlite]'       # adds sqlite-vec
pip install 'chunkshop[clickhouse]'   # adds clickhouse-connect
pip install 'chunkshop[all-backends]' # all three
```

Or in dev:

```bash
uv sync --extra dev --extra all-backends
```

### Internal changes (relevant only if you build on chunkshop's Rust trait surface)
- Direct YAML parser changed from `serde_yml` to `serde_yaml_ng` in v0.4.1
  for supply-chain hygiene (the former was flagged unsound + unmaintained
  per RUSTSEC-2025-0067/0068). YAML semantics unchanged; same `serde`
  derives, same field shape.
- The `BackendDialect` + `BackendConn` traits gained GATs in R2; users
  consuming the trait surface should see the v0.4.0 R2 sub-project release
  notes for the migration.

### If you want to try the new backends
See [`engines/`](engines/) for per-engine connection / DSN / sink-mode
references, and [`mixing-sources-and-sinks.md`](mixing-sources-and-sinks.md)
for how to compose a source from one engine with a sink on another.

---

## Future upgrade notes

When breaking changes ship in future releases, sections will be added here
ahead of the release date with concrete migration steps. Pin chunkshop's
minor version in production (`chunkshop~=0.4.0`) if you want to lock the
upgrade surface; semver-major bumps are the only releases that may require
code changes.
