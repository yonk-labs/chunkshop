"""Verified Slack connector entry-point surface.

Registered via the ``chunkshop.sources`` entry point ``slack``.
Consumers configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: slack
      config:
        channels: [C0123456789, C0987654321]  # optional — None = all visible
        oldest: 1700000000.0                  # optional — epoch seconds
        oauth_tokens: ${SLACK_OAUTH_TOKENS}   # optional — env fallback used
                                              #   if omitted

OAuth tokens are produced by
:class:`chunkshop_connectors.oauth.slack.SlackOAuthProvider`. The config
field accepts a serialised ``OAuthTokens`` dict (the same shape the
dataclass produces); the connector resolves it lazily on first API call.

Sync mode is ``cursor`` — cursor shape is a per-channel map of the
highest Slack ``ts`` seen so far::

    {"C0123456789": "1700000000.000100",
     "C0987654321": "1700000005.000200"}

This is **merge-delta** semantics: each emitted document contributes a
``{channel_id: ts}`` entry, and consumers merge them into the running
cursor in iteration order. ``cursor_from`` returns the single-key
delta; ``empty_cursor`` returns ``{}``.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from chunkshop_connectors.slack.connector import SlackConnector as Connector


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Serialised OAuthTokens dict. When omitted the connector reads
    # $SLACK_OAUTH_TOKENS (JSON-encoded) at first API call.
    oauth_tokens: Optional[dict] = None
    # Channel IDs (preferred) or names the bot can see. None = list all
    # accessible via conversations.list.
    channels: Optional[list[str]] = None
    # How far back to fetch on first sync (epoch seconds). None = beginning.
    # Once a cursor is established this is ignored — the per-channel
    # max-ts wins.
    oldest: Optional[float] = None
    # API base — override for tests / corp proxies.
    slack_base_url: str = "https://slack.com/api"

    def __repr__(self) -> str:
        # Redact oauth_tokens in repr — they hold xoxb-/xoxe- secrets
        # that must never leak to logs.
        oauth_repr = "<redacted>" if self.oauth_tokens else None
        return (
            f"ConfigModel(channels={self.channels!r}, oldest={self.oldest!r}, "
            f"oauth_tokens={oauth_repr}, "
            f"slack_base_url={self.slack_base_url!r})"
        )


def factory(config: dict[str, Any]) -> Connector:
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
