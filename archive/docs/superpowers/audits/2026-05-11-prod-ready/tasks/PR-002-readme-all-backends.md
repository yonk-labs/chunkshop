# PR-002 — README install step includes `--extra all-backends`

**Priority:** P1
**Effort:** XS (~30 min)
**Dependencies:** none
**GAP-IDs:** GAP-016

## Problem

The README quickstart says `cd python && uv sync --extra dev`. This installs chunkshop without the backend driver libraries (`pymysql`, `clickhouse-connect`, `sqlite-vec`). A user picks any non-PG backend → ingest immediately fails with `ModuleNotFoundError`.

Confirmed during the v0.4.0 RT validation in this session: a freshly-bootstrapped worktree venv was missing `pymysql`, breaking the cross-language MariaDB test. Same trap awaits external users.

**Evidence:**
```bash
# Following the README literally:
cd python && uv sync --extra dev

# Then:
chunkshop ingest --config docs/samples/sample-mariadb.yaml
# → ModuleNotFoundError: No module named 'pymysql'
```

## Solution

Update install commands everywhere they appear. The fix is a one-token change: `--extra dev` → `--extra dev --extra all-backends`.

### Files to update

- [ ] `README.md` — top-level "User journey" section, around the `uv sync` line.
- [ ] `python/README.md` — Python install section.
- [ ] `docs/getting-started.md` — Install step.
- [ ] `docs/tutorial.md` — If install commands appear.
- [ ] `docs/tutorial-bakeoff.md`, `docs/tutorial-*.md` — Anywhere `uv sync` appears.
- [ ] `CLAUDE.md` — Developer install section (`uv sync --extra dev --extra extractors`); add `--extra all-backends`.
- [ ] CI workflow files (`.github/workflows/*.yml` or equivalent) — install command in test job.

### Grep to confirm coverage

```bash
grep -rn "uv sync --extra dev" \
  README.md python/README.md docs/ CLAUDE.md .github/ 2>/dev/null
```

Every hit should have `--extra all-backends` (and `--extra extractors` if extractor tests run there).

### The replacement command

Recommended canonical form:

```bash
uv sync --extra dev --extra extractors --extra all-backends
```

(`extractors` for RAKE/NLTK; `all-backends` for `pymysql + clickhouse-connect + sqlite-vec`.)

## Acceptance Criteria

- [ ] Every `uv sync --extra dev` reference in tracked docs has `--extra all-backends` appended.
- [ ] A fresh `git clone` + the README's quoted install step + `chunkshop ingest --config docs/samples/sample-mariadb.yaml` succeeds without `ImportError`.
- [ ] Verify the same for SQLite (`sample-sqlite.yaml`) and ClickHouse (`sample-clickhouse.yaml`) cells.

## Risk if Skipped

Every first-time user picking a non-PG backend hits a confusing import error within seconds of completing the README install. Some will recover by reading the per-engine docs (which now explain the extras). Some will file an issue. Some will give up and pick a different tool.

Closes the only CERTAIN × MAJOR risk in the audit.
