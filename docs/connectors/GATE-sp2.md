# SP-2 Gate Record

Snapshot of the SP-2 (chunkshop-connectors bulk port) gate run.

## Suites

| Suite                                            | Result                                             |
|--------------------------------------------------|----------------------------------------------------|
| `python/connectors/` (full)                      | **68 passed** in ~0.5s                             |
| `python/` (main chunkshop, baseline 547)         | **547 passed, 102 skipped, 6 pre-existing failed** |

The 6 main-suite failures are all in `test_cli_search.py` and
`test_search_result.py`, and they trace to `lede>=0.4.5` not being
installed in this venv. They are pre-existing (independent of SP-2
work — no `src/chunkshop/**` files were modified in this branch).
See CLAUDE.md's "I-1" section for the lede-extra install command.

## Entry points registered

```
['airtable', 'asana', 'bitbucket', 'blob', 'box', 'confluence',
 'dingtalk', 'discord', 'dropbox', 'gcs', 'gitlab', 'gmail',
 'imap', 'jira', 'moodle', 'notion', 'oci', 'r2', 'rest_api',
 'rss', 'seafile', 'sharepoint', 'teams', 'webdav', 'zendesk']
```

25 names total: 2 verified (`blob`, `rss`) + 23 experimental.

## Tag

`sp2-connectors-v0.1`
