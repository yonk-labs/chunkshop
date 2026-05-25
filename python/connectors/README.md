# chunkshop-connectors

First-party connector plugins for [chunkshop](https://github.com/yonk-labs/chunkshop).
Each connector is one Python factory registered against the
`chunkshop.sources` entry-point group; chunkshop core ships with **no**
connectors of its own, so installing this package is how you get
GitHub / Google Drive / Slack / RSS / S3-compatible blob / and ~20 more
sources into chunkshop's `Source → Chunker → Embedder → Sink` pipeline.

## Status

Alpha (v0.1). This package is the bulk-port phase of SP-2; the in-flight
plan is at
`docs/superpowers/plans/2026-05-25-sp2-chunkshop-connectors-bulk-port.md`
in the chunkshop repo.

Two tiers:

- **verified** — `blob`, `rss`, `github`, `gdrive`, `slack`. Behaviourally
  tested against hermetic per-provider mocks; cursor / prune semantics
  exercised via `chunkshop.testing.assert_cursor_advances` and friends.
- **experimental** — everything else (notion, confluence, jira, dropbox,
  box, gitlab, bitbucket, gmail, imap, discord, airtable, asana,
  zendesk, sharepoint, teams, seafile, webdav, moodle, dingtalk,
  rest_api…). Imports + registers; full behaviour not yet certified.

The tier of a connector is set by `@verified` / `@experimental` decorators
in `_tier.py` and readable at runtime via `tier_of(Connector)`.

## Install

```bash
# Just the package skeleton — no SDKs pulled
pip install chunkshop-connectors

# A specific connector's SDK deps
pip install 'chunkshop-connectors[blob]'   # boto3 for S3-compatible
pip install 'chunkshop-connectors[rss]'    # feedparser

# Everything
pip install 'chunkshop-connectors[all]'

# Editable (this monorepo)
pip install -e python/connectors
```

## Use

Once installed, the connector is discoverable through chunkshop's normal
config:

```yaml
source:
  type: connector
  connector: github
  config:
    repo: yonk-labs/chunkshop
    branch: main
    token: ${GITHUB_TOKEN}
```

```python
from chunkshop.config import ConnectorSource
from chunkshop.sources import load_source

src = load_source(ConnectorSource(
    type="connector", connector="github",
    config={"repo": "yonk-labs/chunkshop", "branch": "main", "token": "..."},
))
for doc in src.iter_documents():
    ...
```

## Licence + attribution

Lifted from RAGFlow's MIT-licensed `common/data_source/` subtree, which
itself credits Onyx (formerly Danswer). See `NOTICE`,
`THIRD-PARTY-LICENSES.md`, and `src/chunkshop_connectors/_PROVENANCE.md`
for the audit trail.
