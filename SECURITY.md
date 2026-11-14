# Security Policy

## Supported versions

Only the latest released 1.x minor version receives security fixes
(Python package `chunkshop` and Rust crates `chunkshop-rs` and
`chunkshop-parsers`). Older versions, including all 0.x releases, should
upgrade.

| Version | Supported |
|---------|-----------|
| Latest 1.x minor | :white_check_mark: |
| Older 1.x minors | :x: |
| 0.x | :x: |

## Reporting a vulnerability

**Do not open a public issue for security-sensitive reports.**

Report privately via GitHub's Security Advisories tab on the repository:

https://github.com/yonk-labs/chunkshop/security/advisories/new

If you can't use that flow, email **matt@theyonk.com** with the subject
line starting `[chunkshop-security]`.

In your report, include:

- What the vulnerability allows (info disclosure, RCE, SQL injection, etc.)
- A minimal reproducer — YAML config + command, or a short Python snippet
- Affected version(s) and platform(s)
- Your suggested fix if you have one

## What to expect

- Acknowledgement within **3 business days**.
- Triage + severity assessment within **7 business days**.
- For high/critical issues: a private fix branch, coordinated disclosure
  timeline (default: 30 days to fix + release before public disclosure).

## Scope

**In scope:**

- Code in `python/src/chunkshop/` and `rust/chunkshop/src/` — source, framer,
  chunker, embedder, extractor, sink, runner, orchestrator/CLI surfaces.
- Code in `rust/chunkshop-parsers/src/` — the optional crate that parses
  untrusted PDF / Office / HTML files, including its `isolate` worker
  (`chunkshop-parsers-worker`).
- Shipped YAML configs in `python/src/chunkshop/configs/` and
  `docs/samples/`.
- GitHub Actions workflows under `.github/workflows/`.

**Out of scope (report upstream):**

- `fastembed`, `psycopg`, `pydantic`, or other dependencies — report to
  their maintainers.
- ONNX models hosted on Hugging Face — the hosting provider controls the
  weights.
- Postgres / pgvector, MariaDB, SQLite/sqlite-vec, or ClickHouse itself.

## Hardening notes

For reference, chunkshop's current hardening posture:

- SQL identifiers (`database`/schema, `table`, `source_tag`, promoted column
  names, document table names) are regex-allowlisted at config-load time
  before any generated SQL uses them.
- SQL values are bound parameters. Generated SQL may use f-strings or format
  strings only for trusted fragments: quoted/allowlisted identifiers,
  allowlisted type/operator/language literals, or backend-owned templates.
- The `source` column is write-once on `ON CONFLICT` upserts — provenance
  is preserved across collisions.
- Pydantic's `extra="forbid"` catches typos or hostile extra fields in
  YAML at parse time.
- No generative-API calls in the default ingest path. Optional extractors
  (`keybert_phrases`, `spacy_entities`, `lang_detect`) use local models
  only.

If you find a regression against any of the above, treat it as a security
issue.
