# Releasing chunkshop

chunkshop ships two packages from one repo at the same version:

- **PyPI:** `chunkshop` (Python) — https://pypi.org/project/chunkshop/
- **crates.io:** `chunkshop-rs` (Rust) — https://crates.io/crates/chunkshop-rs

Both are published from a single `.github/workflows/release.yml` workflow
triggered by a semver tag push (`v*.*.*`). The workflow verifies that
the tag matches both `python/pyproject.toml` and `rust/Cargo.toml`
versions before publishing.

The two publish paths run in parallel and are independent — a PyPI
failure does not block crates.io and vice-versa.

## One-time maintainer setup

### PyPI Trusted Publishing (no API token to manage)

1. Visit https://pypi.org/manage/account/publishing/.
2. Add a new trusted publisher:
   - Owner: `yonk-labs`
   - Repository: `chunkshop`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. In this repo, create a GitHub Environment named `pypi`
   (Settings → Environments → New). Optionally add required reviewers
   for a manual approval gate before each PyPI publish.

### crates.io API token

1. Visit https://crates.io/me → "API Tokens" → "New Token".
2. Scopes: `publish-new` + `publish-update`. Set an expiry (e.g.
   1 year) and a memorable name (`chunkshop-release`).
3. Copy the token. You only see it once.
4. In this repo: Settings → Secrets and variables → Actions →
   New repository secret:
   - Name: `CARGO_REGISTRY_TOKEN`
   - Value: the token from step 3
5. Optionally create a GitHub Environment named `cargo` for required-
   reviewer protection (same shape as `pypi`).

## Cutting a release

For every release, follow this loop:

```bash
# 1. Pick the next version (semver). Both packages move together.
NEW="0.5.0"

# 2. Bump source-of-truth versions. Both must match the tag exactly.
#    Python: edit python/pyproject.toml, change [project].version.
#    Rust:   edit rust/Cargo.toml, change [workspace.package].version.
#    The verify job in the workflow rejects the tag otherwise.

# 2b. Regenerate the Rust lockfile so it records the new version.
#     ⚠️ THE #1 RELEASE GOTCHA: CI builds with `cargo --locked`, which FAILS
#     the crates.io publish if rust/Cargo.lock is out of sync with Cargo.toml.
#     Bumping [workspace.package].version leaves the lock stale until you do:
(cd rust && cargo update --workspace)   # rewrites rust/Cargo.lock to $NEW

# 3. Update CHANGELOG.md with what's in the release.

# 4. Commit, push to main. NOTE: include rust/Cargo.lock — omitting it is the
#    stale-lock failure that silently blocks the crates.io half of the release.
git add python/pyproject.toml rust/Cargo.toml rust/Cargo.lock CHANGELOG.md
git commit -m "release: v$NEW"
git push origin main

# 5. Create + push the tag. The workflow runs on this push.
git tag "v$NEW"
git push origin "v$NEW"

# 6. Watch the run at github.com/yonk-labs/chunkshop/actions.
#    If you set required-reviewer environments, click through to
#    approve each publish job.
```

## Verifying a release

After the workflow finishes:

- **PyPI:** `pip install chunkshop==$NEW` — should pull the new wheel.
  The package page at https://pypi.org/project/chunkshop/ shows the
  new version and the rendered `python/README.md`.
- **crates.io:** `cargo install chunkshop-rs --version $NEW` — pulls
  and builds. The crate page at https://crates.io/crates/chunkshop-rs
  shows the new version and the rendered `rust/README.md`.

## What happens on a failed publish

| Failure | Effect | Recovery |
|---|---|---|
| Tag/source version mismatch | Whole workflow fails before any publish | Push a corrected tag, OR delete the bad tag (`git tag -d` + `git push --delete`) and update the source files |
| Python build error | PyPI publish skipped; cargo publish still runs | Fix the Python build; bump the patch (e.g. v0.4.1) and retag |
| PyPI upload error (e.g. token / OIDC config) | PyPI publish fails; cargo publish unaffected | Fix the trusted-publisher config and retag, OR upload manually with `uv build && twine upload dist/*` |
| `cargo test` failure | `build-cargo` job fails before `publish-cargo`; PyPI unaffected | Fix the test, retag |
| Stale `rust/Cargo.lock` (version bump didn't regen it) | `cargo --locked` fails the cargo job before `publish-cargo`; PyPI may still publish, leaving the two packages out of sync | `(cd rust && cargo update --workspace)`, commit `rust/Cargo.lock`, **move the tag to the fixed commit** (`git tag -f vX.Y.Z <sha> && git push -f origin vX.Y.Z`) and re-trigger. Caught earlier by the `--locked` pre-flight above |
| crates.io upload error (e.g. token expired) | `publish-cargo` fails; PyPI unaffected | Refresh `CARGO_REGISTRY_TOKEN` and retag |

**Yanking a release:**

- PyPI: https://pypi.org/manage/project/chunkshop/release/$NEW/ →
  "Options" → "Yank release". Yanked versions stay installable for
  pinned consumers but are excluded from `pip install`'s version
  resolution.
- crates.io: `cargo yank --version $NEW chunkshop-rs`. Same semantics.

You cannot republish the same version after yanking — bump and tag a
new patch.

## What we publish, what we don't

**PyPI (`chunkshop` package):**
- Source distribution (sdist) + wheel from `python/`
- Excludes: tests, samples, the rust/ tree
- Optional extras: `extractors`, `keybert`, `spacy`, `lang`, `nlp`,
  `quantize`, `lede`, `lede-spacy`, `sumy`, `s3`, `dev`

**crates.io (`chunkshop-rs` crate):**
- The library + the `chunkshop-rs` binary, from `rust/chunkshop/`
- Excludes (via `Cargo.toml [package].exclude`):
  - `tests/parity-fixtures/*` — multi-MB JSON / `.bin` references
    used for cross-language byte-identicality tests, only meaningful
    inside the repo
- Optional features: `lede` (pulls `lede` from crates.io to enable
  the `chunkshop.summarizers.lede` callable summarizer module)

The bundled sample corpora (`docs/samples/bakeoff-ntsb/corpus/`,
`docs/samples/sales-crm/sql/`, `docs/samples/sales-crm/notes.tar.gz`)
do NOT ship in either package — they're under `docs/`, outside both
package roots.

## Pre-flight checks for the next release

Before pushing the tag:

```bash
# Confirm the branch is clean and the PR/release checks are green.
git status --short
gh pr checks <pr-number> --repo yonk-labs/chunkshop

# Python: build cleanly into dist/.
(cd python && uv build --sdist --wheel && ls dist/)
# Should produce chunkshop-$VERSION.tar.gz and chunkshop-$VERSION-py3-*.whl.

# Rust: package + dry-run publish. --locked mirrors CI exactly: it FAILS if
# rust/Cargo.lock is stale vs Cargo.toml (the #1 release gotcha — catch it here,
# not in the publish workflow after the tag is already pushed).
(cd rust && cargo publish --dry-run --locked -p chunkshop-rs)
# Should end with "warning: aborting upload due to dry run".

# Run full test suites once more.
(cd python && uv run pytest -q)
(cd rust && cargo test --workspace --lib --locked)

# Dependency audits. Current Rust upstream waivers are documented in the
# dependency-audit workflow and MariaDB engine docs.
(cd python && uv export --frozen --all-extras --no-hashes --no-emit-project \
  --output-file /tmp/chunkshop-pip-audit.txt)
(cd python && uv run --with pip-audit pip-audit -r /tmp/chunkshop-pip-audit.txt \
  --no-deps --disable-pip --skip-editable)
(cd rust && cargo audit --ignore RUSTSEC-2023-0071 --ignore RUSTSEC-2024-0436)

# Confirm feature-support docs match implementation before release:
# target.documents is Python/Postgres-only until Rust/Postgres parity lands.

# Lint sample YAMLs (every YAML in docs/samples/ should parse).
(cd python && uv run python -c "
import yaml, glob
from chunkshop.config import CellConfig
from chunkshop.bakeoff.config import BakeoffConfig
for p in sorted(glob.glob('../docs/samples/**/*.yaml', recursive=True)):
    with open(p) as f: data = yaml.safe_load(f)
    if not data: continue
    name = list(data.keys())[0] if isinstance(data, dict) else '?'
    print(p, '... ok')
")
```

If all four pass, push the tag.
