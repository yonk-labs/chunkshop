"""OAuth providers for chunkshop connectors.

Each provider implements the ``chunkshop.oauth.OAuthProvider``
``Protocol`` (from chunkshop core). Providers wrap the per-vendor
quirks (Google requires ``access_type=offline`` + ``prompt=consent``
to issue a refresh_token; Slack issues bot tokens with the v2 OAuth
endpoint; etc.) so connector code can stay vendor-agnostic.

Available providers
-------------------
* :class:`chunkshop_connectors.oauth.google.GoogleOAuthProvider`
* :class:`chunkshop_connectors.oauth.slack.SlackOAuthProvider`
"""
from __future__ import annotations

from chunkshop_connectors.oauth.google import GoogleOAuthProvider
from chunkshop_connectors.oauth.slack import SlackOAuthProvider

__all__ = ["GoogleOAuthProvider", "SlackOAuthProvider"]
