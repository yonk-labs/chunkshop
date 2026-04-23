<!--
Thanks for the PR! Fill in the sections below — short is better than
comprehensive. Delete sections that don't apply.
-->

## What this changes

<!-- One paragraph: what the change does and why. Link the issue if there is one. -->

## Type of change

<!-- Check all that apply -->

- [ ] `feat` — new user-facing feature
- [ ] `fix` — bug fix
- [ ] `docs` — documentation only
- [ ] `test` — tests only
- [ ] `refactor` — no behavior change
- [ ] `build` — pyproject / dependencies / package config
- [ ] `ci` — GitHub Actions workflows

## Verification

<!-- How did you confirm this works? Delete rows that don't apply. -->

- [ ] `cd python && uv run pytest -q` passes locally.
- [ ] `bash tests/sub/run-all.sh` passes (needs `CHUNKSHOP_TEST_DSN`).
- [ ] `bash tests/use-cases/run-all.sh` passes.
- [ ] For new providers: unit test fails without the change and passes with it.
- [ ] For behavior changes: scenario added or updated in `tests/sub/scenarios/` or `tests/use-cases/scenarios/`.

## Documentation

- [ ] User-facing changes reflected in the relevant `docs/` file(s).
- [ ] `CHANGELOG.md` entry added under "Unreleased" for any behavior change.
- [ ] If this is a breaking change, noted as such and justified.

## Dependencies

- [ ] No new runtime dependencies (or: new dep is justified in the description).
- [ ] `uv.lock` committed if `pyproject.toml` dependencies changed.

## Pre-merge

- [ ] No hardcoded credentials, `.env` files, or real API keys in the diff.
- [ ] No generated files committed (model weights, ONNX blobs, result JSONs from `skill-output/`).
