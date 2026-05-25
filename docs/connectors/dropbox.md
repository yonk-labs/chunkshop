# Dropbox connector

The `dropbox` connector walks a Dropbox folder via the v2 REST API
and yields one chunkshop `Document` per text-shaped file. It's part
of the verified tier — behaviourally tested against a hermetic
`httpx.MockTransport`-backed mock under
`chunkshop_connectors.testing.mocks.dropbox`.

## What you get

* One `Document` per text-extension file under `folder_path`.
* `Document.id` = the file's `path_lower` (lowercase Dropbox path).
* `Document.title` = the file's base name (`README.md`).
* `Document.content` = the file body, UTF-8 decoded.
* `Document.metadata` carries
  `{dropbox_id, server_modified, rev, size, path_display, dropbox_cursor}`.

Non-text files (anything outside the `include_extensions` allow-list)
are silently skipped. UTF-8 decode failure on an allow-listed file
emits a `UserWarning` and the file is dropped.

## Authentication: OAuth bearer / app access token

Auth is **bearer token**. Dropbox supports both short-lived OAuth
tokens and long-lived app access tokens; the connector treats them
identically and never tries to refresh.

For server-side ingest, the simplest setup is:

1. **App Console → Create app** with the **Scoped access** model.
2. Pick **App folder** (sandboxed) or **Full Dropbox** depending on
   what you need to ingest.
3. On the app's **Permissions** tab enable:
   * `files.metadata.read` (for `list_folder`)
   * `files.content.read` (for `download`)
4. On the **Settings** tab, click **Generate** under the OAuth 2 →
   "Generated access token" section. Capture the token.

### Token resolution

Precedence: `config.token` > `$DROPBOX_TOKEN` env var. If neither is
set, requests go out without `Authorization` and Dropbox returns
401.

`ConfigModel.__repr__` redacts the token to `***`.

## Configuration

```yaml
source:
  type: connector
  connector: dropbox
  config:
    folder_path: "/Apps/chunkshop"      # default "" = account root
    recursive: true                     # default true
    include_extensions:                 # default = common text MIMEs
      - ".md"
      - ".txt"
    token: ${DROPBOX_TOKEN}             # optional — env fallback
```

| Key                  | Type              | Required | Notes                                                       |
|----------------------|-------------------|----------|-------------------------------------------------------------|
| `folder_path`        | string            | no       | Dropbox path; `""` (default) is the account / app-folder root. |
| `recursive`          | bool              | no       | Default `true`.                                             |
| `include_extensions` | list of strings   | no       | Lowercase file extensions to keep. Default = common text types. |
| `token`              | string            | no       | App / OAuth access token. Falls back to `$DROPBOX_TOKEN`.   |
| `base_url`           | string            | no       | Override for testing.                                       |
| `content_url`        | string            | no       | Override for testing (Dropbox content host).                |

### Default text extensions

If `include_extensions` is omitted, the connector emits files ending
in any of: `.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.yml`, `.rst`,
`.log`. Other extensions are silently skipped at list-time, before
any download cost is paid.

## Sync mode

`sync_mode = SyncMode.CURSOR`. The cursor shape is:

```json
{"cursor": "<opaque dropbox cursor>"}
```

* **First sync** (empty cursor): calls `POST /2/files/list_folder`
  from scratch, walks all pages, and stashes the final cursor
  returned by Dropbox.
* **Subsequent syncs**: calls
  `POST /2/files/list_folder/continue?cursor=<stored>`. Dropbox
  emits only entries that changed since the cursor was minted; the
  response carries a new cursor.

Dropbox cursors are opaque, monotonic, and account-scoped. The
connector never tries to construct or interpret one.

## Limitations

* No prune support — deleted files are not surfaced as `Document`
  deletions. The connector does not implement `PrunableSource`.
* No Dropbox Paper / Dropbox Sign integration. Only files in the
  filesystem-shaped `files/...` API surface are emitted.
* PDFs, DOCX, images, etc. are silently skipped unless they
  literally match `include_extensions`. Pre-process with
  `yonk-doctools` and feed the markdown output via the `files`
  source instead.
* Team-folder ingest requires a team-scoped token; this connector
  has only been exercised against personal / app-folder tokens.
