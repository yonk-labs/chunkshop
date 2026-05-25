"""Hermetic tests for ``chunkshop_connectors.oauth.google.GoogleOAuthProvider``.

All HTTP is intercepted by ``httpx.MockTransport`` — no live OAuth
calls. The autouse loopback-only socket guard in ``conftest.py`` is
respected because ``MockTransport`` short-circuits before any socket
is opened.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


def _mock_transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Build a ``MockTransport`` that dispatches on the request path.

    ``routes`` maps URL paths (e.g. ``"/token"``) to ``httpx.Response``
    objects. Unknown paths get a 404 so test failures are explicit.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        resp = routes.get(request.url.path)
        if resp is None:
            return httpx.Response(404, text=f"no mock for {request.url.path}")
        return resp

    return httpx.MockTransport(handler)


def test_implements_protocol():
    from chunkshop.oauth import OAuthProvider
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    p = GoogleOAuthProvider(client_id="cid", client_secret="csec")
    assert isinstance(p, OAuthProvider)


def test_authorization_url_includes_offline_access_and_required_params():
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    p = GoogleOAuthProvider(client_id="cid", client_secret="x")
    url = p.authorization_url(
        state="random-state-123",
        redirect_uri="https://cb.example/callback",
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    # access_type=offline is required for Google to issue a refresh_token.
    assert qs["access_type"] == ["offline"]
    # prompt=consent forces the consent screen so a refresh_token is
    # returned even on subsequent auths.
    assert qs["prompt"] == ["consent"]
    assert qs["response_type"] == ["authorization_code".replace("authorization_", "")] or qs["response_type"] == ["code"]
    assert qs["client_id"] == ["cid"]
    assert qs["redirect_uri"] == ["https://cb.example/callback"]
    assert qs["state"] == ["random-state-123"]
    # scopes joined by space per RFC 6749
    assert qs["scope"] == ["https://www.googleapis.com/auth/drive.readonly"]


def test_authorization_url_multiple_scopes_space_joined():
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    p = GoogleOAuthProvider(client_id="cid", client_secret="x")
    url = p.authorization_url(
        state="s", redirect_uri="https://cb",
        scopes=["drive.readonly", "drive.metadata.readonly"],
    )
    qs = parse_qs(urlparse(url).query)
    assert qs["scope"] == ["drive.readonly drive.metadata.readonly"]


def test_exchange_code_returns_tokens():
    from chunkshop.oauth import OAuthTokens
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    routes = {
        "/token": httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/drive.readonly",
                "token_type": "Bearer",
            },
        )
    }
    p = GoogleOAuthProvider(
        client_id="cid",
        client_secret="x",
        transport=_mock_transport(routes),
    )
    before = datetime.now(timezone.utc)
    t = p.exchange_code(code="auth_code", redirect_uri="https://cb")
    after = datetime.now(timezone.utc)

    assert isinstance(t, OAuthTokens)
    assert t.access_token == "AT"
    assert t.refresh_token == "RT"
    assert t.provider == "google"
    assert "https://www.googleapis.com/auth/drive.readonly" in t.scopes
    # expires_at should be ~now + 3600s
    assert before + timedelta(seconds=3500) <= t.expires_at <= after + timedelta(seconds=3700)


def test_exchange_code_sends_required_form_params():
    """The token endpoint POST must include grant_type, code, redirect_uri,
    client_id, client_secret per the OAuth 2.0 spec / Google docs."""
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 3600,
                "scope": "x",
                "token_type": "Bearer",
            },
        )

    p = GoogleOAuthProvider(
        client_id="cid",
        client_secret="csec",
        transport=httpx.MockTransport(handler),
    )
    p.exchange_code(code="auth_code", redirect_uri="https://cb")
    assert captured["method"] == "POST"
    assert "/token" in captured["url"]
    form = parse_qs(captured["body"])
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["auth_code"]
    assert form["redirect_uri"] == ["https://cb"]
    assert form["client_id"] == ["cid"]
    assert form["client_secret"] == ["csec"]


def test_refresh_token_preserves_old_refresh_token_when_google_omits_it():
    """Google often returns no ``refresh_token`` on refresh — the provider
    must keep the old one so the consumer doesn't lose offline access."""
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    routes = {
        "/token": httpx.Response(
            200,
            json={
                "access_token": "AT2",
                "expires_in": 3600,
                "scope": "drive.readonly",
                "token_type": "Bearer",
            },
        )
    }
    p = GoogleOAuthProvider(
        client_id="cid",
        client_secret="x",
        transport=_mock_transport(routes),
    )
    t = p.refresh_token("old-rt")
    assert t.access_token == "AT2"
    # Old refresh token survives.
    assert t.refresh_token == "old-rt"
    assert t.provider == "google"


def test_refresh_token_uses_new_refresh_token_when_google_returns_one():
    """When Google does return a fresh refresh_token (e.g. rotation), use it."""
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    routes = {
        "/token": httpx.Response(
            200,
            json={
                "access_token": "AT2",
                "refresh_token": "NEW_RT",
                "expires_in": 3600,
                "scope": "drive.readonly",
                "token_type": "Bearer",
            },
        )
    }
    p = GoogleOAuthProvider(
        client_id="cid",
        client_secret="x",
        transport=_mock_transport(routes),
    )
    t = p.refresh_token("old-rt")
    assert t.refresh_token == "NEW_RT"


def test_refresh_token_sends_grant_type_refresh_token():
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "access_token": "AT2",
                "expires_in": 3600,
                "scope": "drive.readonly",
                "token_type": "Bearer",
            },
        )

    p = GoogleOAuthProvider(
        client_id="cid",
        client_secret="csec",
        transport=httpx.MockTransport(handler),
    )
    p.refresh_token("rt-old-xyz")
    form = parse_qs(captured["body"])
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["rt-old-xyz"]
    assert form["client_id"] == ["cid"]
    assert form["client_secret"] == ["csec"]


def test_validate_scopes_subset():
    from chunkshop.oauth import OAuthTokens
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    p = GoogleOAuthProvider(client_id="cid", client_secret="x")
    tok = OAuthTokens(
        access_token="x",
        refresh_token=None,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["drive.readonly", "drive.metadata.readonly"],
        provider="google",
        provider_extras={},
    )
    assert p.validate_scopes(tok, ["drive.readonly"]) is True
    assert p.validate_scopes(tok, ["drive.readonly", "drive.metadata.readonly"]) is True
    assert p.validate_scopes(tok, []) is True
    # Missing scope → False
    assert p.validate_scopes(tok, ["drive.write"]) is False
    assert p.validate_scopes(tok, ["drive.readonly", "drive.write"]) is False


def test_exchange_code_raises_on_http_error():
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    routes = {
        "/token": httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "bad code"}
        )
    }
    p = GoogleOAuthProvider(
        client_id="cid",
        client_secret="x",
        transport=_mock_transport(routes),
    )
    with pytest.raises(httpx.HTTPStatusError):
        p.exchange_code(code="bad", redirect_uri="https://cb")
