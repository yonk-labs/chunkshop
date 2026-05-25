# Slack connector

The `slack` connector walks the channels a Slack bot can see and yields
one chunkshop `Document` per **message** — top-level messages and
thread replies each get their own `Document`. It's part of the verified
tier — behaviourally tested against a hermetic `httpx.MockTransport`-backed
mock under `chunkshop_connectors.testing.mocks.slack`.

## What you get

* One `Document` per message in every channel the bot can read (or per
  message in the channels you specify).
* `Document.id` = `"<channel_id>::<ts>"` — Slack's `ts` is unique
  per-channel and stable, so the composite is globally unique and
  re-emit-safe.
* `Document.title` = `"<channel_name>:<ts>"`.
* `Document.content` = the message's `text` field. Empty for
  file-share-only / bot-payload-only messages — the chunker decides
  whether to keep them.
* `Document.metadata` carries `{channel_id, channel_name, user_id, ts}`,
  plus `thread_ts` for messages in a thread and `subtype` (e.g.
  `channel_join`, `bot_message`) when set.

Thread parents emit their own `Document` *and* trigger a fan-out to
`conversations.replies`; each reply gets its own `Document` with the
parent's `ts` in `metadata["thread_ts"]`. Sort downstream by `ts` to
reconstruct chronological order.

## Authentication: OAuth 2.0 (bot token)

Auth is **OAuth 2.0 bot bearer token**. Tokens come from
`chunkshop_connectors.oauth.slack.SlackOAuthProvider` — the connector
itself doesn't run the consent flow.

### Required OAuth scopes

For read-only access (recommended), use:

| Scope              | Why                                                |
|--------------------|----------------------------------------------------|
| `channels:history` | Read messages in public channels via `conversations.history`. |
| `channels:read`    | List public channels via `conversations.list`.     |
| `groups:history`   | Read messages in private channels the bot is in.   |
| `groups:read`      | List private channels.                             |
| `users:read`       | Resolve user IDs to names (optional — connector doesn't call `users.info` today). |
| `team:read`        | Carry team metadata on tokens (informational).     |

The bot must be **explicitly added** to every channel you want to
ingest from (`/invite @your-bot`). Slack's permission model is channel-
level — having `channels:history` doesn't grant access to channels the
bot isn't a member of.

### Producing the tokens

Run the consent flow once via `SlackOAuthProvider`:

```python
from chunkshop_connectors.oauth.slack import SlackOAuthProvider
from dataclasses import asdict
import json

prov = SlackOAuthProvider(
    client_id="<your-cid>",
    client_secret="<your-csec>",
)
print(prov.authorization_url(
    state="csrf-nonce",
    redirect_uri="http://localhost:8765/cb",
    scopes=["channels:history", "channels:read", "users:read", "team:read"],
))
# ... user grants consent in browser, callback receives ?code=...
tokens = prov.exchange_code(
    code="<from-callback>",
    redirect_uri="http://localhost:8765/cb",
)
print(json.dumps(asdict(tokens), default=str, indent=2))
```

Pass the resulting dict to the connector via `config.oauth_tokens`
**or** set it as the env var `SLACK_OAUTH_TOKENS` (JSON-encoded).

### Bot tokens vs user tokens

Slack's OAuth v2 splits scopes into **bot** (`scope=...`) and **user**
(`user_scope=...`). The default is bot-only — chunkshop reads channel
history via the bot token, which keeps the perms surface narrow.

If you need a user token (xoxp-) for endpoints that aren't available to
bots, pass `user_scopes=[...]` to `authorization_url`. The user token
lands in `OAuthTokens.provider_extras["user_access_token"]`; the
top-level `access_token` stays the bot token.

### Token refresh

The connector does **not** auto-refresh tokens at runtime — that's the
orchestrator's job. Use
`chunkshop.oauth.proactive_refresh(tokens, provider=...)` to refresh
within the access-token's expiry window before passing the tokens to
the connector. Slack supports refresh-token rotation for workspaces
that opt in; the provider always prefers the server's new
`refresh_token` when present and falls back to the prior one for
non-rotating bot tokens.

### Never log the tokens

`ConfigModel.__repr__` redacts `oauth_tokens` to `<redacted>`. The
connector class does the same. `OAuthTokens.__repr__` (from chunkshop
core) also redacts access/refresh tokens. Don't `print(cfg.model_dump())`.

## Configuration

```yaml
source:
  type: connector
  connector: slack
  config:
    channels:                          # optional — None means "all visible"
      - C0123456789
      - C0987654321
    oldest: 1700000000.0               # optional — epoch-seconds floor
    oauth_tokens: ${SLACK_OAUTH_TOKENS} # optional — env fallback used if omitted
```

| Key             | Type                  | Required | Notes                                                       |
|-----------------|-----------------------|----------|-------------------------------------------------------------|
| `channels`      | list of strings       | no       | Channel IDs the bot can read. `null` walks `conversations.list`. |
| `oldest`        | float (epoch seconds) | no       | First-sync floor. Ignored on incremental syncs (cursor wins). |
| `oauth_tokens`  | dict                  | no       | Serialised `OAuthTokens`. If omitted, reads `$SLACK_OAUTH_TOKENS`. |
| `slack_base_url`| string                | no       | Defaults to `https://slack.com/api`. Override for proxies / tests. |

## Sync mode

`sync_mode = SyncMode.CURSOR`. The cursor shape is a per-channel map:

```json
{
  "C0123456789": "1700000000.000100",
  "C0987654321": "1700000005.000200"
}
```

* **First sync** (`empty_cursor() == {}`):
  1. List channels (or use the configured set).
  2. For each channel, paginate `conversations.history` with
     `oldest=config.oldest`.
  3. For every thread parent (`thread_ts == ts and reply_count > 0`),
     fan out to `conversations.replies`.
  4. Sort each channel's messages ascending-by-ts before emitting so
     the merge-delta cursor settles on the highest ts per channel.

* **Subsequent syncs**:
  1. For each channel, use `oldest = cursor.get(channel_id, config.oldest or 0)`.
  2. Slack's `oldest` is **exclusive** — the boundary message isn't
     re-emitted.
  3. The cursor is merged via `chunkshop.testing.merge_cursor` (i.e.
     `dict.update` in iteration order); since each channel's docs are
     ascending-by-ts, the last doc per channel wins and carries the
     correct max.

### Prune support

Not supported in this tier. Slack's API doesn't expose a clean
"deleted since X" stream for messages — message deletes generate
`message_deleted` events on the RTM API that we don't subscribe to.
If you need deletion detection, run a periodic full resync.

## Rate limits

Slack's documented per-method tiers (from
https://api.slack.com/apis/rate-limits):

| Method                    | Tier | Limit         |
|---------------------------|------|---------------|
| `conversations.list`      | 2    | ~20/min       |
| `conversations.history`   | 3    | ~50/min       |
| `conversations.replies`   | 3    | ~50/min       |
| `users.info`              | 4    | ~100/min      |

Slack's **non-marketplace** apps face a much tighter cap for
`conversations.history` (1 req/min as of 2025-09 rollout). The
connector does **not** back off or retry on `ratelimited` errors in
v1 — it raises immediately. For large-corpus ingest you want to be a
Slack marketplace app and budget your concurrency. Add a custom
`httpx` event hook + retry layer if you need exponential backoff.

## Testing

The connector ships with a hermetic mock at
`chunkshop_connectors.testing.mocks.slack.slack_mock`. It uses
`httpx.MockTransport` (in-process, no socket) so the autouse
loopback-only socket guard in the connectors test suite is satisfied
without any special config.

Slack API JSON shapes match the published reference responses
(`conversations.list`, `conversations.history`, `conversations.replies`,
`users.info`) — see https://api.slack.com/methods for the canonical
schemas.
