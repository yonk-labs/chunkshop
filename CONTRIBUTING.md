# Contributing to chunkshop

Thanks for considering a contribution. chunkshop is heading into 1.0 (the
release-candidate series is on PyPI and crates.io), and the bar for PRs is
"small, explained, with tests" — not "matches a 6-month roadmap."

## What to expect

- **Scope:** chunkshop is an ingest pipeline, not a retrieval framework,
  an LLM orchestrator, or an observability tool. See
  [`docs/executive-summary.md`](docs/executive-summary.md) for the
  boundary.
- **Two implementations.** Python (`python/`) is the reference and has the
  full feature surface; Rust (`rust/`, published as `chunkshop-rs`) covers
  the core pipeline, all 4 backends, and the bakeoff, with its own test
  suite (`cargo test`). Pipeline changes usually need parity in both — see
  the parity table in [`README.md`](README.md). Go is planned but not
  started.
- **Pre-1.0, APIs can still move.** Breaking changes go in `CHANGELOG.md`
  under the release they land in.

## Setup

```bash
git clone https://github.com/yonk-labs/chunkshop.git
cd chunkshop/python
uv sync --extra dev --extra extractors --extra nlp
```

For integration tests, you need Postgres with pgvector:

```bash
docker run -d --name chunkshop-pg -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 pgvector/pgvector:pg16
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5432/postgres"
```

Tests that need Postgres skip cleanly if `CHUNKSHOP_TEST_DSN` is unset.

## Running tests

```bash
# Unit + integration (skipping DB tests if DSN unset):
cd python && uv run pytest -q

# Functional scenario smoke tests (needs DB):
bash tests/sub/run-all.sh
uv run --project python python tests/sub/assert-row-counts.py

# Narrative use-case scenarios (needs DB):
bash tests/use-cases/run-all.sh
uv run --project python python tests/use-cases/assert-row-counts.py
```

CI runs all of the above on every PR.

## Adding a provider

New source / framer / chunker / embedder / extractor:

1. Drop a file into `python/src/chunkshop/{package}/`.
2. Add a pydantic config model to `python/src/chunkshop/config.py` with a
   unique `type` literal; include it in the relevant `XConfig` union.
3. Add a branch to `load_X` in `python/src/chunkshop/{package}/__init__.py`.
4. Write a unit test in `python/tests/chunkshop/test_X.py`.
5. Document the provider in the matching `docs/X.md`:
   - **What it does** (one paragraph).
   - **When to pick it** (decision-tree row + a non-fit case).
   - **Config fields + defaults** (table).
   - **Sample output**.
6. Consider adding a scenario to `tests/use-cases/scenarios/` if this
   provider fits a real business use case the existing scenarios don't
   cover.

## PR checklist

Before submitting:

- [ ] `uv run pytest -q` passes locally.
- [ ] New code has unit tests that fail without the change.
- [ ] User-facing changes documented in the right `docs/` file(s).
- [ ] Behavior changes and new features noted in `CHANGELOG.md` under
      "Unreleased".
- [ ] If you touched `python/pyproject.toml` dependencies, you ran
      `uv sync` and committed the updated `uv.lock`.
- [ ] No hardcoded credentials, `.env` files, or real API keys. Run
      `/secret-scan` or equivalent if unsure.

## Commit messages

Conventional-commits-ish: `type(scope): subject`. Types in use:

- `feat` — new user-facing feature
- `fix` — bug fix
- `docs` — documentation only
- `test` — tests only
- `refactor` — no behavior change
- `build` — pyproject / dependencies / package config
- `ci` — GitHub Actions workflows

Example: `feat(chunkers): semantic chunker with embedding-drift boundaries`.

Squash-merge is the default for PRs. Keep PR descriptions short — the
commit message is what lands in history.

## Reporting bugs / asking questions

- **Bugs:** open an issue using the bug-report template.
- **Feature requests:** open an issue using the feature-request template.
- **Security:** see [`SECURITY.md`](SECURITY.md) — do not open a public
  issue.
- **Questions:** open a GitHub Discussion if you think others would
  benefit; otherwise a regular issue is fine.

## License

By contributing, you agree that your contributions will be licensed under
the MIT License that covers the project. See [`LICENSE`](LICENSE).
