# GitHub connector

The `github` connector walks a GitHub repository at a given branch and
yields one chunkshop `Document` per text file. It's part of the
verified tier — behaviourally tested against a hermetic
`pytest_httpserver`-backed mock under
`chunkshop_connectors.testing.mocks.github`.

## What you get

* One `Document` per text file under the branch's tree.
* `Document.id` and `Document.title` = the file's path within the repo.
* `Document.content` = the file body, UTF-8 decoded.
* `Document.metadata` carries `{path, size, sha, branch, branch_sha}`.

Binary files (anything that doesn't decode as UTF-8) are skipped
silently with a `UserWarning`. There is no "include binaries" knob in
this tier — vector embedding of binary blobs is out of scope.

## Authentication: PAT-only

The connector authenticates with a **Personal Access Token**, supplied
either via `config.token` or the `GITHUB_TOKEN` environment variable
(config wins if both are set). OAuth is intentionally out of scope —
this connector targets server-side ingest where a CI / orchestrator
already holds a long-lived token.

### Required PAT scopes

| Repo visibility | Required classic-PAT scope | Fine-grained-PAT permission                            |
|-----------------|----------------------------|--------------------------------------------------------|
| Public          | `public_repo`              | **Contents**: Read-only on the target repository       |
| Private         | `repo`                     | **Contents**: Read-only on the target repository       |

No write scopes are ever used or required — the connector is strictly
read-only.

### Never log the token

`ConfigModel.__repr__` redacts the token to `***`. The connector class
does the same. If you write your own logging around the connector,
keep this in mind — don't `print(cfg.model_dump())`.

## Configuration

```yaml
source:
  type: connector
  connector: github
  config:
    owner: acme
    repo: widgets
    branch: main                 # optional — omit to auto-detect default
    branch_strict: false         # optional — error on missing pinned branch
    clone: false                 # optional — shallow-clone + local walk
    max_clone_mb: 200            # optional — refuse clones over this size
    paths_glob:                  # optional, default = all files
      - "**/*.md"
      - "src/**/*.py"
    token: ${GITHUB_TOKEN}       # optional — env var used if omitted
    base_url: https://api.github.com   # optional, override for GH Enterprise
```

| Key             | Type            | Required | Notes                                                            |
|-----------------|-----------------|----------|------------------------------------------------------------------|
| `owner`         | string          | yes      | Org or user that owns the repo. Matches `^[A-Za-z0-9-]+$`.       |
| `repo`          | string          | yes      | Repo name. Matches `^[A-Za-z0-9._-]+$`.                          |
| `branch`        | string          | no       | Omit to auto-detect the repo's `default_branch`. Full name, not a SHA. |
| `branch_strict` | bool            | no       | Default `false`. If `true`, a missing pinned branch is a hard error (no fallback). |
| `clone`         | bool            | no       | Default `false`. If `true`, shallow-clone the repo and walk the tree locally instead of one API call per file. Needs the `git` binary; falls back to the REST walk if absent. |
| `max_clone_mb`  | int             | no       | Default `200`. Refuse to process a shallow clone larger than this. |
| `paths_glob`    | list of strings | no       | Each pattern is matched against the file path. `**` allowed.     |
| `token`         | string          | no       | PAT. If omitted, the connector reads `GITHUB_TOKEN` from the env.|
| `base_url`      | string          | no       | Defaults to `https://api.github.com`. Set for GitHub Enterprise. |

### Branch auto-detection

If you omit `branch`, the connector probes `GET /repos/{owner}/{repo}`
and uses the repo's reported `default_branch`. This sidesteps the most
common gotcha — repos whose default is `master`, not `main`. If you
*do* pin a branch and it 404s, the connector falls back to the default
branch and retries once, unless `branch_strict: true`.

### Clone mode vs. REST walk

By default the connector reads each file via the REST API
(`GET /contents/{path}`), which is one request per file. For large
repos that's slow and quota-hungry (see [Rate limits](#rate-limits)).
Set `clone: true` to instead `git clone --depth 1` the branch once and
walk the working tree locally — a single network fetch regardless of
file count. Clone mode needs the `git` binary on `PATH`; if it's
missing, the connector warns and falls back to the REST walk. Private
repos clone over HTTPS with the PAT inlined into the URL.

### `paths_glob` semantics

* If omitted, every blob in the tree is candidate-emitted.
* Each pattern is matched with segment-aware globbing — `*` does *not*
  cross `/`, and `**` matches zero or more path segments.
* A file is emitted if **any** pattern matches.

Examples:

| Pattern        | `README.md` | `src/a.py` | `src/lib/b.py` | `docs/x.md` |
|----------------|:-----------:|:----------:|:--------------:|:-----------:|
| `*.md`         | yes         | no         | no             | no          |
| `**/*.md`      | yes         | no         | no             | yes         |
| `src/**/*.py`  | no          | yes        | yes            | no          |

## Sync mode

`sync_mode = SyncMode.CURSOR`. The cursor shape is:

```json
{"after_commit_sha": "<sha>"}
```

* **First sync** (`empty_cursor() == {}`): the connector emits every
  file matching `paths_glob` at the current head, then advances the
  cursor to that head's SHA.
* **Subsequent syncs**: the connector calls
  `GET /repos/{owner}/{repo}/compare/{after_commit_sha}...{branch}`
  and emits only files in the diff that are still present (added /
  modified). Deletions are not surfaced as Documents — this connector
  does **not** implement `PrunableSource`. Source-side deletions are a
  follow-up if needed.

### `StaleCursorError`

If GitHub returns **HTTP 422** from `/compare/...` (typically because
the cursor's SHA is no longer reachable from the branch — force-push,
deleted ref, history rewrite), the connector raises
`chunkshop.sources.base.StaleCursorError`. The consumer should drop
the cursor and call `iter_changes_since(empty_cursor())` to do a full
resync.

### Prune support

Not supported in this tier. If you need source-side deletion
detection, run a periodic full resync into a fresh table and switch
the chunkshop ingest sink over once it completes — or wait for a
follow-up that adds `PrunableSource` here.

## Rate limits

The default REST walk uses the un-cached endpoints, so each file costs
one `contents/{path}` call. For a 5,000-file repo on a token with the
default 5,000 req/h limit, expect to consume the bulk of the hourly
budget on a full resync. Plan your `refresh_freq_seconds` accordingly
and prefer incremental syncs.

To avoid the per-file API cost entirely, set `clone: true` — a full
sync then costs a single `git clone` fetch instead of N REST calls.
Incremental syncs still use the REST `/compare` endpoint (a handful of
calls), so clone mode is most valuable for the first/full sync of a
large repo.

## GitHub Enterprise

Set `base_url: https://github.example.com/api/v3` and everything else
behaves the same — the connector doesn't bake the `api.github.com`
host into any path.
