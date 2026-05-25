# Notion connector

The `notion` connector walks a Notion workspace via the v1 REST API
and yields one chunkshop `Document` per page. It's part of the
verified tier — behaviourally tested against a hermetic
`httpx.MockTransport`-backed mock under
`chunkshop_connectors.testing.mocks.notion`.

## What you get

* One `Document` per Notion page (either every page in a database or
  the explicit `page_ids` set).
* `Document.id` = the Notion page UUID.
* `Document.title` = the page's title property, or the page ID if no
  title property exists.
* `Document.content` = the page's block tree, reduced to plain text
  with markdown-style heading / list prefixes. See "Block walker"
  below.
* `Document.metadata` carries
  `{notion_id, last_edited_time, created_time, parent_type, max_last_edited_time}`.

## Authentication: integration token

The connector authenticates with a **Notion integration token** —
not OAuth. Generate one in your workspace's integration settings:
**Settings & members → Connections → Develop or manage integrations
→ New integration → Internal**. Capture the secret (`secret_...`).

Then **share** the target pages / databases with the integration
(open the page, click Share, search for your integration name, add
it with the "Can read" permission).

### Required capabilities

| Operation                               | Required capability |
|-----------------------------------------|---------------------|
| Reading pages and blocks                | **Read content**    |
| Querying databases                      | **Read content**    |

No write capabilities are ever requested or required.

### Token resolution

Precedence: `config.token` > `$NOTION_TOKEN` env var. If neither is
set, the connector still makes API calls — Notion will reject them
with 401, which surfaces as `httpx.HTTPStatusError`.

`ConfigModel.__repr__` redacts the token to `***`.

## Configuration

```yaml
source:
  type: connector
  connector: notion
  config:
    database_id: 0a1b2c3d-4e5f-...      # OR page_ids
    # page_ids: ["abc...", "def..."]
    token: ${NOTION_TOKEN}              # optional — env fallback
    notion_version: "2022-06-28"        # optional, pins API version
```

| Key             | Type             | Required | Notes                                              |
|-----------------|------------------|----------|----------------------------------------------------|
| `database_id`   | string (UUID)    | one of   | UUID of a database; emits every page it contains.  |
| `page_ids`      | list of strings  | one of   | Explicit page UUIDs; emits each.                   |
| `token`         | string           | no       | Integration secret. Falls back to `$NOTION_TOKEN`. |
| `notion_version`| string           | no       | Notion-Version header. Default `2022-06-28`.       |
| `base_url`      | string           | no       | Override for testing / API mirrors.                |

Exactly one of `database_id` or `page_ids` must be supplied.

## Sync mode

`sync_mode = SyncMode.CURSOR`. The cursor shape is:

```json
{"after_last_edited_time": "<ISO8601 timestamp>"}
```

* **First sync** (empty cursor): walks every page in the database (or
  every entry in `page_ids`), then advances the cursor to the maximum
  `last_edited_time` observed across emitted pages.
* **Subsequent syncs**: filters the database query body with
  `filter.timestamp = "last_edited_time"` and
  `filter.last_edited_time.on_or_after = <cursor>`. Pages whose
  `last_edited_time` exactly equals the cursor are skipped (already
  emitted on the prior sync).
* For `page_ids` mode the connector fetches each page and emits only
  those whose `last_edited_time` is strictly greater than the cursor.

### Block walker

The connector reduces each page's block tree to plain text via a
depth-first walk:

| Block type                       | Emitted as                |
|----------------------------------|---------------------------|
| `paragraph`, `callout`, `toggle` | plain line                |
| `heading_1` / `_2` / `_3`        | `#`, `##`, `###` prefix   |
| `bulleted_list_item`             | `- ` prefix               |
| `numbered_list_item`             | `1. ` prefix              |
| `to_do`                          | `- [x]` / `- [ ]` prefix  |
| `quote`                          | `> ` prefix               |
| `code`                           | fenced ``` ``` ``` ``` block |
| `divider`, `image`, `video`, `file`, `embed`, `bookmark`, `unsupported`, `child_database`, `child_page` | silently skipped |

Nested children (blocks with `has_children = true`) are walked
recursively up to depth 16; deeper recursion is clipped to prevent
runaway content from stack-overflowing.

## Limitations

* No OAuth support — internal integration tokens only. If you need
  multi-tenant OAuth flow, build it on top of this connector or wait
  for a v2 OAuth lift.
* No prune support — deletions / archives are not surfaced. The
  connector does not implement `PrunableSource`.
* Database queries paginate at 100 pages per round-trip.
* Images, embeds, file blocks, and child databases inside pages are
  silently dropped — pre-process with `yonk-doctools` if you need
  them surfaced as text.
