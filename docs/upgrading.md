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

## Media ingest

**TL;DR:** Nothing to do. Upgrading a **text-only** deployment needs no new
installs, adds no dependencies, creates no files, changes no table schema and
produces byte-identical rows. Media is opt-in per cell: a `files` source with
no `derive:` block behaves exactly as it did before.

This is measured, not asserted — an audit ran pre-existing text-only configs
against the pre-media baseline and against this release into separate targets
and diffed the rows byte-for-byte, including the raw embedding blobs. The full
result, and the list of what was *not* measured, is in
[`media-ingest.md` → Upgrade impact](media-ingest.md#upgrade-impact).

### The one thing to know: `on_empty` is derive-scoped

`source.on_empty` defaults to `"error"`, but **it governs derivers only**. If
a document is routed to a `derive:` rung and that rung returns no text, the
cell raises `EmptyDerivationError` naming the file and the deriver — a model
returning nothing is a failure, not a zero-length success, and it must not
vanish silently from the index.

A document handled by an **ordinary parser** — a plain `.md`, or a `.pdf` in a
cell with no `derive:` rung for it — that yields empty text is indexed as an
empty document regardless of this setting. That is the pre-`on_empty`
behaviour, kept unconditionally for the plain-parser path.

So: **if you have no `derive:` block, `on_empty` cannot fire at all,** and
this default is not reachable from your configs. Full semantics in
[`media-ingest.md` → The empty-derivation policy](media-ingest.md#the-empty-derivation-policy-on_empty).

### Action required

**None for text-only cells.** `on_empty` only becomes relevant once you add a
`derive:` block. When you do, decide then between `error` (default — stop and
tell me), `skip` (omit the document, continue the run) and `index` (index the
empty document; kept for backward compatibility, not recommended).

### What this release does *not* fix: scanned PDFs

If you came here expecting the scanned-PDF gap to be closed, it is not. **No
shipped rung derives text from a PDF** — rungs 0 and 1 take images, rung 5
takes images, audio and video. A scanned, image-only PDF still extracts to
nothing and is still indexed as an empty document, and because `on_empty` is
derive-scoped it does not catch that case. Closing it needs the OCR rung
(rung 2), which is not built. See
[`media-ingest.md` → Limits of this release](media-ingest.md#limits-of-this-release).

### Rust-only differences

Two behaviours changed for the Rust binary, both of them Rust catching up to
Python rather than new behaviour:

- **`files`-source rows gain a `parser` metadata key** (`"parser":
  "TextParser"` for plain text). Python has always written it. Low impact
  unless you promote or schema-validate the metadata object.
- **Unknown fields under a `files` `source:` block are now rejected** at
  config load. Python has always rejected these (pydantic `extra="forbid"`).
  This affects only a config that already carried a key Rust was silently
  dropping. Run `chunkshop-rs validate` over your configs before rolling out.

---

## Future upgrade notes

When breaking changes ship in future releases, sections will be added here
ahead of the release date with concrete migration steps. Pin chunkshop's
minor version in production (`chunkshop~=0.4.0`) if you want to lock the
upgrade surface; semver-major bumps are the only releases that may require
code changes.
