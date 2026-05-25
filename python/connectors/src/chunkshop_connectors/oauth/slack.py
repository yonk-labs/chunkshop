"""Slack OAuth v2 provider for chunkshop connectors.

Implements :class:`chunkshop.oauth.OAuthProvider` against Slack's
``/api/oauth.v2.access`` token endpoint. Like the Google provider, this
module only talks HTTP — no ``slack-sdk`` dependency. ``httpx`` is the
only runtime dep; tests inject ``httpx.MockTransport`` via the
``transport`` constructor kwarg.

Slack-specific quirks codified here
-----------------------------------
* Slack's OAuth v2 uses **comma-separated** scope lists, not the
  space-separated form RFC 6749 specifies. The ``scope=`` parameter
  carries bot scopes; ``user_scope=`` carries user scopes. We default
  to bot-only — chunkshop's slack connector reads channel history via
  the bot token, which keeps the perms surface narrower than asking
  for a user token.
* The token response wraps the BOT token at the top-level
  ``access_token`` field; the USER (xoxp-) token, when ``user_scope``
  was set, is nested under ``authed_user.access_token``. The provider
  stores the user token in ``OAuthTokens.provider_extras`` so the
  typed surface stays single-token (bot is primary).
* Slack returns HTTP 200 for *every* response, including errors. The
  ``ok`` field is the actual success/failure signal — ``{"ok": false,
  "error": "..."}`` payloads must be raised, not silently treated as
  success.
* Slack supports rotating refresh tokens (for workspaces that opt in).
  When the response includes a fresh ``refresh_token``, we use it;
  when it doesn't (legacy non-rotating bot tokens), we preserve the
  caller-supplied one so offline access keeps working.
* Slack's non-rotating bot tokens do not include ``expires_in`` — they
  don't expire. We synthesise a 1-hour ``expires_at`` so callers can
  still use ``chunkshop.oauth.proactive_refresh`` uniformly across
  providers, but the refresh is a no-op for non-rotating tokens.

References
----------
* https://api.slack.com/authentication/oauth-v2
* https://api.slack.com/methods/oauth.v2.access
* https://api.slack.com/authentication/rotation
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

from chunkshop.oauth.tokens import OAuthTokens


_DEFAULT_TOKEN_ENDPOINT = "https://slack.com/api/oauth.v2.access"
_DEFAULT_AUTH_ENDPOINT = "https://slack.com/oauth/v2/authorize"


class SlackOAuthError(Exception):
    """Raised when Slack returns ``{"ok": false}``.

    Slack reports OAuth errors with HTTP 200 + an ``ok`` field — there's
    no HTTP-level signal we can lean on. This exception surfaces the
    ``error`` slug from the response so consumers don't get a silent
    half-success.
    """


class SlackOAuthProvider:
    """Slack OAuth v2 provider.

    Parameters
    ----------
    client_id, client_secret
        From the Slack app's "Basic Information" / "OAuth & Permissions"
        page in api.slack.com.
    token_endpoint, auth_endpoint
        Overridable for testing or for users behind a corp proxy.
    transport
        Optional ``httpx.BaseTransport`` (typically ``MockTransport``)
        injected by tests to short-circuit network calls. Production
        leaves this ``None`` so ``httpx`` uses its default transport.
    timeout
        Per-request timeout in seconds. Defaults to 30s.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        token_endpoint: str = _DEFAULT_TOKEN_ENDPOINT,
        auth_endpoint: str = _DEFAULT_AUTH_ENDPOINT,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_endpoint = token_endpoint
        self._auth_endpoint = auth_endpoint
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            f"SlackOAuthProvider(client_id={self._client_id!r}, "
            f"client_secret=<redacted>, token_endpoint={self._token_endpoint!r})"
        )

    # ------------------------------------------------------------------
    # OAuthProvider protocol
    # ------------------------------------------------------------------
    def authorization_url(
        self,
        state: str,
        redirect_uri: str,
        scopes: list[str],
        *,
        user_scopes: Optional[list[str]] = None,
    ) -> str:
        """Return the Slack consent URL the user should be redirected to.

        ``scopes`` populates ``scope=`` (bot scopes — the default surface
        chunkshop reads). ``user_scopes``, when provided, populates
        ``user_scope=`` for xoxp- user tokens. Slack uses **comma**-
        separated lists for both, not space-separated.
        """
        # Comma-join — Slack OAuth v2 expects "scope=a,b,c", not "a b c".
        params: dict[str, str] = {
            "client_id": self._client_id,
            "scope": ",".join(scopes),
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if user_scopes:
            params["user_scope"] = ",".join(user_scopes)
        return f"{self._auth_endpoint}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        """POST the authorization code to Slack's token endpoint."""
        resp = self._client.post(
            self._token_endpoint,
            data={
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        return self._tokens_from_payload(payload, prior_refresh_token=None)

    def refresh_token(self, refresh_token: str) -> OAuthTokens:
        """Exchange a refresh_token for a fresh access_token.

        Slack supports rotation — when the response carries a new
        ``refresh_token`` we use it. For legacy non-rotating bot tokens
        Slack omits the field and we preserve the caller's value.
        """
        resp = self._client.post(
            self._token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        return self._tokens_from_payload(
            payload, prior_refresh_token=refresh_token
        )

    def validate_scopes(self, tokens: OAuthTokens, required: list[str]) -> bool:
        """Return True iff every required scope is present on the tokens."""
        return set(required).issubset(set(tokens.scopes))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _tokens_from_payload(
        self, payload: dict, *, prior_refresh_token: Optional[str]
    ) -> OAuthTokens:
        """Construct an ``OAuthTokens`` from a Slack token response.

        Raises ``SlackOAuthError`` when ``ok`` is false (Slack reports
        OAuth failures with HTTP 200 + ``ok: false``).
        """
        if not payload.get("ok", False):
            err = payload.get("error", "unknown_error")
            raise SlackOAuthError(f"slack oauth.v2.access failed: {err}")

        # Slack splits scopes on commas, not spaces. The Google provider
        # uses split() (space-separated) — don't be tempted to copy that
        # pattern here.
        scope_str = payload.get("scope", "")
        scopes = [s.strip() for s in scope_str.split(",") if s.strip()]

        # Slack's non-rotating bot tokens omit ``expires_in``. We
        # synthesise a 1h horizon so proactive_refresh has something to
        # work with even though the refresh is effectively a no-op.
        expires_in = int(payload.get("expires_in", 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        # Rotation: prefer the server's new refresh_token; fall back to
        # whatever the caller passed in. On initial exchange_code, prior
        # is None and Slack only sends refresh_token if the workspace
        # has rotation enabled.
        refresh = payload.get("refresh_token") or prior_refresh_token

        # User token (xoxp-) lives under authed_user when user_scope
        # was requested. Stash in provider_extras — the typed
        # access_token stays the bot token.
        authed_user = payload.get("authed_user") or {}
        extras: dict = {
            "token_type": payload.get("token_type"),
            "team_id": (payload.get("team") or {}).get("id"),
            "team_name": (payload.get("team") or {}).get("name"),
            "bot_user_id": payload.get("bot_user_id"),
            "app_id": payload.get("app_id"),
            "authed_user_id": authed_user.get("id"),
        }
        # Only carry user_access_token if it's actually present and
        # non-empty — empty string is Slack's way of saying "no user
        # scope was requested".
        user_at = authed_user.get("access_token") or None
        if user_at:
            extras["user_access_token"] = user_at
            extras["user_scope"] = authed_user.get("scope", "")
        # Strip None values for compactness in logs / serialisation.
        extras = {k: v for k, v in extras.items() if v is not None}

        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=refresh,
            expires_at=expires_at,
            scopes=scopes,
            provider="slack",
            provider_extras=extras,
        )
