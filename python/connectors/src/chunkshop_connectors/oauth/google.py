"""Google OAuth 2.0 provider for chunkshop connectors.

Implements :class:`chunkshop.oauth.OAuthProvider` for Google's OAuth
endpoints (Drive, Gmail, Calendar, ...). The provider only talks
HTTP — it does not depend on ``google-auth`` / ``google-api-python-client``,
both of which are heavy and awkward to mock hermetically. ``httpx`` is
the only runtime dependency; tests inject ``httpx.MockTransport`` via
the optional ``transport`` constructor kwarg.

Google-specific quirks codified here
------------------------------------
* ``access_type=offline`` is required on the consent URL or Google
  refuses to issue a refresh token. (Authorization Code flow defaults
  to "online", which gives back only an access_token.)
* ``prompt=consent`` is required to ensure a refresh_token is
  re-issued even if the user has previously granted consent — without
  it Google returns no refresh_token on repeat auths and offline access
  silently breaks.
* On ``grant_type=refresh_token``, Google often omits ``refresh_token``
  in the response (only rotates it occasionally). The provider
  preserves the *old* refresh_token if the response doesn't carry a
  new one, so consumers never lose long-lived offline access.

References
----------
* https://developers.google.com/identity/protocols/oauth2/web-server
* https://developers.google.com/identity/protocols/oauth2#expiration
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

from chunkshop.oauth.tokens import OAuthTokens


_DEFAULT_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_DEFAULT_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"


class GoogleOAuthProvider:
    """Google OAuth 2.0 provider.

    Parameters
    ----------
    client_id, client_secret
        From the Google Cloud Console "OAuth 2.0 Client IDs" page.
    token_endpoint, auth_endpoint
        Overridable for testing or for users behind a corp proxy.
    transport
        Optional ``httpx.BaseTransport`` (typically ``MockTransport``)
        injected by tests to short-circuit network calls. Production
        code leaves this ``None`` and lets ``httpx`` use its default
        transport.
    timeout
        Per-request timeout in seconds. Defaults to 30s — long enough
        for slow corporate proxies, short enough that a hung token
        endpoint doesn't wedge a whole ingest run.
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
        # httpx.Client honours a None transport (default) cleanly.
        self._client = httpx.Client(transport=transport, timeout=timeout)

    # ------------------------------------------------------------------
    # Repr — never leak secrets even if a consumer prints the provider.
    # ------------------------------------------------------------------
    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            f"GoogleOAuthProvider(client_id={self._client_id!r}, "
            f"client_secret=<redacted>, token_endpoint={self._token_endpoint!r})"
        )

    # ------------------------------------------------------------------
    # OAuthProvider protocol
    # ------------------------------------------------------------------
    def authorization_url(
        self, state: str, redirect_uri: str, scopes: list[str]
    ) -> str:
        """Return the Google consent URL the user should be redirected to.

        ``access_type=offline`` + ``prompt=consent`` are required for
        Google to return a refresh_token alongside the access_token.
        """
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{self._auth_endpoint}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens:
        """POST the authorization code to Google's token endpoint."""
        resp = self._client.post(
            self._token_endpoint,
            data={
                "grant_type": "authorization_code",
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

        Google's response often omits ``refresh_token`` (it only
        rotates the refresh_token under specific conditions). When the
        field is absent we re-use the caller-supplied refresh_token so
        offline access keeps working.
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
        return self._tokens_from_payload(payload, prior_refresh_token=refresh_token)

    def validate_scopes(self, tokens: OAuthTokens, required: list[str]) -> bool:
        """Return True iff every required scope is present on the tokens."""
        return set(required).issubset(set(tokens.scopes))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _tokens_from_payload(
        self, payload: dict, *, prior_refresh_token: Optional[str]
    ) -> OAuthTokens:
        """Construct an ``OAuthTokens`` from a Google token response.

        ``prior_refresh_token`` is the refresh token the caller passed
        in (only on refresh, not on exchange). It's used as a fallback
        when Google's response omits ``refresh_token`` so we don't lose
        offline access on a refresh round-trip.
        """
        expires_in = int(payload.get("expires_in", 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        # Google returns ``scope`` as a space-separated string.
        scope_str = payload.get("scope", "")
        scopes = scope_str.split() if scope_str else []
        refresh = payload.get("refresh_token") or prior_refresh_token
        # provider_extras carries non-standard fields (token_type,
        # id_token if present) for consumers that care, without
        # leaking them into the typed surface.
        extras = {
            k: v
            for k, v in payload.items()
            if k not in {"access_token", "refresh_token", "expires_in", "scope"}
        }
        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=refresh,
            expires_at=expires_at,
            scopes=scopes,
            provider="google",
            provider_extras=extras,
        )
