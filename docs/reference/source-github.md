# `github` connector

**Module**: `chunkshop_connectors.github`
**Type**: Source (verified-tier connector)
**Ship status**: verified
**Optional extra**: `chunkshop-connectors[github]` (httpx)
**Since**: 2026-05-25 (commit `74d51f3`)

## Purpose

Walk a GitHub repository at a given branch via the REST API and yield one
chunkshop `Document` per text file. Auth is PAT-only — Personal Access
Token, supplied either through the YAML `config.token` field or via the
`GITHUB_TOKEN` env var. Incremental sync uses GitHub's `/compare` endpoint
to emit only the files that changed since the last cursor SHA.

## Config schema

`chunkshop_connectors.github.ConfigModel` (pydantic v2, `extra="forbid"`):

| Field        | Type            | Default                           | Notes |
|--------------|-----------------|-----------------------------------|-------|
| `owner`      | `str`           | **Required**                      | GitHub user/org. Regex-checked `^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?$`. |
| `repo`       | `str`           | **Required**                      | Repo slug. Regex-checked `^[A-Za-z0-9._-]+$`. |
| `branch`     | `str`           | `"main"`                          | Branch name. Regex-checked `^[A-Za-z0-9._/\-]+$`. |
| `paths_glob` | `list[str]?`    | `None` (all files)                | Per-pattern glob with optional `**`. See "Glob matching" below. |
| `token`      | `str?`          | `None` → falls back to `$GITHUB_TOKEN` | PAT. Redacted in `__repr__`. |
| `base_url`   | `str`           | `"https://api.github.com"`        | Override for GitHub Enterprise or test transports. |

PAT scope requirements:

- Public repos: `public_repo`
- Private repos: `repo`

## Public API

```python
class GitHubConnector:
    sync_mode = SyncMode.CURSOR

    def __init__(self, config: dict[str, Any]) -> None: ...

    # Source
    def iter_documents(self) -> Iterator[Document]: ...

    # IncrementalSource
    def empty_cursor(self) -> dict: ...
    def iter_changes_since(self, cursor: dict) -> Iterable[Document]: ...
    def cursor_from(self, last_document: Document) -> dict: ...
```

Factory: `chunkshop_connectors.github.factory(config: dict) -> GitHubConnector`
— validates `config` against `ConfigModel`, then constructs.

Tier marker: `@verified` (`tier_of(GitHubConnector) == "verified"`).

## Behavior contract

1. **Sync mode is `CURSOR`.** Cursor shape: `{"after_commit_sha": "<sha>"}`.
2. **Empty cursor → full tree walk** at the branch's current head SHA via
   `git/trees/{sha}?recursive=1`, then `contents/{path}` for each blob.
3. **Non-empty cursor → `/compare/{prior}...{branch}`**. Only files with
   status `added` / `modified` are re-emitted. `removed` files are
   silently skipped — this connector does NOT implement `PrunableSource`.
4. **422 from `/compare` → raises `chunkshop.sources.base.StaleCursorError`**.
   This happens when the prior SHA is no longer reachable (force-push,
   branch delete). The consumer drops the cursor and resyncs.
5. **Binary files are skipped with a `UserWarning`.** GitHub doesn't
   advertise MIME type cheaply, so the connector attempts UTF-8 decoding
   and skips on `UnicodeDecodeError`. One image in a repo doesn't kill
   the sync.
6. **Cursor advances monotonically.** Every Document yielded in a single
   sync carries the same `metadata.branch_sha` (the head SHA at sync
   start), so merging cursor deltas converges to the same final cursor
   regardless of iteration order.
7. **Token is redacted in `__repr__`**: prints as `***` so a stray
   `print(connector)` doesn't leak the PAT.
8. **HTTP client is reused.** One `httpx.Client` per connector instance,
   GC-closed.

### Glob matching

`paths_glob` patterns support a single `**` segment. Each pattern is matched
segment-by-segment via `fnmatch.fnmatchcase`. Multi-`**` patterns fall
back to matching the prefix and suffix segments only; the middle is
unconstrained. Known limitation: multi-`**` patterns don't support
arbitrary depth between the `**`s.

## Inputs

- GitHub REST API endpoints: `/branches/{branch}`, `/git/trees/{sha}`,
  `/contents/{path}`, `/compare/{a}...{b}`.
- PAT (optional but recommended — anonymous rate limit is 60 req/hr).
- Optional `paths_glob` filter to restrict which paths are emitted.

## Outputs

Each yielded `Document`:

| Field         | Value |
|---------------|-------|
| `id`          | repo-relative path, e.g. `src/foo.py` |
| `content`     | UTF-8-decoded body |
| `title`       | same as `id` |
| `metadata`    | `{path, size, sha, branch, branch_sha}` |
| `fingerprint` | `None` (cursor-only sync, no fingerprint) |

`metadata.branch_sha` is load-bearing — `cursor_from()` reads it.

## Errors

| Exception | When |
|-----------|------|
| `StaleCursorError` | `/compare` returned 422 (prior SHA unreachable). Consumer must drop cursor and resync. |
| `httpx.HTTPStatusError` | Any non-422 GitHub error (401 invalid token, 404 unknown repo/branch, 403 rate-limited). |
| `pydantic.ValidationError` | At `factory()` time — bad `owner`, `repo`, or `branch` regex; extra keys in `config`. |

## Example: minimal

```yaml
cell_name: my_repo_ingest
source:
  type: connector
  connector: github
  config:
    owner: octocat
    repo: Hello-World
    # token falls back to $GITHUB_TOKEN
  sync: {mode: cursor}
chunker: {type: sentence_aware, max_chars: 2000}
embedder:
  type: fastembed
  model_name: BAAI/bge-small-en-v1.5
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: my_repo
  table: chunks
  mode: overwrite
```

## Example: realistic

```yaml
cell_name: chunkshop_ingest
source:
  type: connector
  connector: github
  config:
    owner: yonk-labs
    repo: chunkshop
    branch: main
    paths_glob:
      - "**/*.py"
      - "**/*.md"
      - "docs/**/*.md"
    token: ${GITHUB_TOKEN}
  sync:
    mode: cursor
    refresh_freq_seconds: 3600
chunker:
  type: symbol_aware
  granularity: function
  include_imports: true
extractor:
  type: composite
  extractors:
    - type: code_summary
      backend: lede
    - type: code_relationships
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: chunkshop_kb
  table: chunks
  mode: overwrite
  source_tag: chunkshop_main
  promote_metadata:
    - {path: symbol_name, type: text}
    - {path: fqn,         type: text}
    - {path: path,        type: text}
    - {path: language,    type: text}
    - {path: summary,     type: text}
```

## How it integrates with the pipeline

`GitHubConnector` is a `Source`. The runner pulls Documents from it
identically to `FilesSource` or `PgTableSource` — the connector
indirection (`type: connector, connector: github`) is purely a discovery
mechanism via the `chunkshop.sources` entry-point group.

When combined with `symbol_aware` + `code_relationships` + `code_summary`,
each file becomes one chunk per top-level symbol, with FQN/callee
metadata stamped. See [`docs/cookbook/code-search.md`](../cookbook/code-search.md).

## Tests proving the contract

- `python/connectors/tests/test_github_connector.py`:
  - registry membership + tier marker
  - `ConfigModel` validation (extra-key rejection, regex rejection)
  - hermetic full-tree walk via `httpx.MockTransport`
  - incremental sync against `/compare` mock
  - `StaleCursorError` on 422
  - binary-file skip with `UserWarning`
  - token redaction in `__repr__`
- `python/connectors/tests/test_e2e_user_expectations.py` —
  end-to-end via mocked GitHub API.
- Live demo: `python/connectors/examples/e2e_github_with_code_chunker.py`.

## See also

- [`docs/connectors/github.md`](../connectors/github.md) — auth + scope guide
- [`docs/connectors/README.md`](../connectors/README.md) — connector tier model
- [`docs/cookbook/code-search.md`](../cookbook/code-search.md) — full code-search recipe
- [`docs/cookbook/incremental-sources.md`](../cookbook/incremental-sources.md) — cursor mechanics
- Reference: [`source-gdrive`](source-gdrive.md), [`source-blob`](source-blob.md)
