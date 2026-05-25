# GitLab connector

The `gitlab` connector walks a GitLab project at a given branch via
the v4 REST API and yields one chunkshop `Document` per text file.
It's part of the verified tier — behaviourally tested against a
hermetic `httpx.MockTransport`-backed mock under
`chunkshop_connectors.testing.mocks.gitlab`.

This connector mirrors the [`github`](github.md) connector almost
exactly — same cursor shape, same binary-file policy, same
`paths_glob` semantics. The only differences are the URL encoding of
the project identifier and the auth header.

## What you get

* One `Document` per text file under the branch's tree.
* `Document.id` and `Document.title` = the file's path within the
  repository.
* `Document.content` = the file body, UTF-8 decoded.
* `Document.metadata` carries `{path, size, blob_id, branch, branch_sha}`.

Binary files (anything that doesn't decode as UTF-8) are silently
skipped with a `UserWarning`.

## Authentication: PAT / project / group token

The connector authenticates with a **Personal Access Token**,
**Group Access Token**, or **Project Access Token** — supplied via
`config.token` or the `GITLAB_TOKEN` environment variable (config
wins if both are set). OAuth is intentionally out of scope.

The token is sent via the `PRIVATE-TOKEN` header, which GitLab
accepts for all three token types.

### Required scopes

| Token type            | Required scope |
|-----------------------|----------------|
| Personal Access Token | `read_api` (or `read_repository` for ref-level reads only) |
| Group Access Token    | `read_api` (or `read_repository`)                          |
| Project Access Token  | `read_api` (or `read_repository`)                          |

No write scopes are ever used.

### Token redaction

`ConfigModel.__repr__` redacts the token to `***`. Don't
`print(cfg.model_dump())` in your own logging.

## Configuration

```yaml
source:
  type: connector
  connector: gitlab
  config:
    project: acme/widgets               # "namespace/project" or "12345"
    branch: main                        # optional, default "main"
    paths_glob:                         # optional, default = all files
      - "**/*.md"
      - "src/**/*.py"
    token: ${GITLAB_TOKEN}              # optional — env fallback
    base_url: https://gitlab.com/api/v4 # optional, override for self-hosted
```

| Key          | Type            | Required | Notes                                                  |
|--------------|-----------------|----------|--------------------------------------------------------|
| `project`    | string          | yes      | `namespace/project` (path) or `12345` (numeric ID).    |
| `branch`     | string          | no       | Default `main`.                                        |
| `paths_glob` | list of strings | no       | Segment-aware glob; `**` matches any depth.            |
| `token`      | string          | no       | Falls back to `$GITLAB_TOKEN`.                         |
| `base_url`   | string          | no       | Default `https://gitlab.com/api/v4`. Override for self-hosted. |

The `project` segment is URL-encoded internally — namespace paths
like `acme/widgets` become `acme%2Fwidgets` on the wire.

### `paths_glob` semantics

Same as github:

* Each pattern is matched with segment-aware globbing — `*` does
  *not* cross `/`, and `**` matches zero or more path segments.
* A file is emitted if any pattern matches.

## Sync mode

`sync_mode = SyncMode.CURSOR`. The cursor shape is:

```json
{"after_commit_sha": "<sha>"}
```

Identical to the github connector.

* **First sync**: walks the tree at the current branch head and emits
  every file matching `paths_glob`, then advances the cursor to that
  head SHA.
* **Subsequent syncs**: calls
  `GET /projects/{project}/repository/compare?from={cursor}&to={head}`
  and emits only files in the diff that are added or modified.
  Deletions are not surfaced — the connector does not implement
  `PrunableSource`.

## Limitations

* No OAuth — PAT-style auth only.
* No prune support; deletions are silently dropped.
* No symbolic-ref resolution — the `branch` config must be the
  literal branch name, not a tag or arbitrary ref.
* No streaming download for large files; the `/repository/files`
  endpoint base64-encodes the body and returns it inline, so very
  large files (multi-MB) are loaded into memory.
* Subgroups are supported as long as the full path fits the
  `namespace/subgroup/project` shape; deeper nesting still works
  but the path validator's allow-list is conservative.
