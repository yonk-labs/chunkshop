"""Hermetic tests for ``chunkshop_connectors.oauth.slack.SlackOAuthProvider``.

All HTTP intercepted by ``httpx.MockTransport`` — no live OAuth calls.
The autouse loopback-only socket guard in ``conftest.py`` is respected
because ``MockTransport`` short-circuits before any socket is opened.

Slack's OAuth v2 quirks codified in these tests
-----------------------------------------------
* Bot vs user scopes are split: ``scope=`` carries the bot scopes,
  ``user_scope=`` carries the user (xoxp) scopes. Default is bot-only.
* ``/api/oauth.v2.access`` returns the bot token at the top-level
  ``access_token``; the user token (if any) is nested under
  ``authed_user.access_token``.
* Slack supports rotating refresh tokens — the response on
  ``grant_type=refresh_token`` carries the new refresh_token and the
  old one is invalidated server-side. We always prefer the new one.
* Unlike Google, Slack rotates on every refresh call when refresh is
  enabled for the workspace — we never fall back to the prior token
  silently.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


def _mock_transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Build a ``MockTransport`` that dispatches on request path."""

    def handler(request: httpx.Request) -> httpx.Response:
        resp = routes.get(request.url.path)
        if resp is None:
            return httpx.Response(404, text=f"no mock for {request.url.path}")
        return resp

    return httpx.MockTransport(handler)


def test_implements_protocol():
    from chunkshop.oauth import OAuthProvider
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    p = SlackOAuthProvider(client_id="cid", client_secret="csec")
    assert isinstance(p, OAuthProvider)


def test_authorization_url_uses_bot_scopes_by_default():
    """Slack's ``scope=`` query parameter carries BOT scopes; ``user_scope=``
    carries user scopes. The default is BOT — the connector reads channel
    history via the bot token.
    """
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    p = SlackOAuthProvider(client_id="cid", client_secret="x")
    url = p.authorization_url(
        state="random-state-xyz",
        redirect_uri="https://cb.example/callback",
        scopes=["channels:history", "channels:read", "users:read", "team:read"],
    )
    parsed = urlparse(url)
    assert parsed.netloc == "slack.com"
    assert parsed.path == "/oauth/v2/authorize"

    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["cid"]
    assert qs["state"] == ["random-state-xyz"]
    assert qs["redirect_uri"] == ["https://cb.example/callback"]
    # Slack uses comma-separated scope lists, not space-separated like RFC 6749.
    assert qs["scope"] == ["channels:history,channels:read,users:read,team:read"]
    # No user_scope by default → either absent or empty string.
    assert "user_scope" not in qs or qs["user_scope"] in ([""], [])


def test_exchange_code_returns_bot_token_by_default():
    """Slack's ``access_token`` at the top level is the BOT token (xoxb-).
    The connector consumes the bot token for read APIs; the user token is
    only relevant when user_scope was requested.
    """
    from chunkshop.oauth import OAuthTokens
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    routes = {
        "/api/oauth.v2.access": httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-BOT-TOKEN",
                "token_type": "bot",
                "scope": "channels:history,channels:read,users:read,team:read",
                "bot_user_id": "U_BOT",
                "app_id": "A12345",
                "team": {"id": "T1", "name": "test-team"},
                "enterprise": None,
                "authed_user": {"id": "U_AUTH", "scope": "", "access_token": ""},
            },
        )
    }
    p = SlackOAuthProvider(
        client_id="cid",
        client_secret="x",
        transport=_mock_transport(routes),
    )
    before = datetime.now(timezone.utc)
    t = p.exchange_code(code="auth_code", redirect_uri="https://cb")
    after = datetime.now(timezone.utc)

    assert isinstance(t, OAuthTokens)
    assert t.access_token == "xoxb-BOT-TOKEN"
    assert t.provider == "slack"
    assert "channels:history" in t.scopes
    assert "channels:read" in t.scopes
    # expires_at: Slack doesn't issue expiring tokens by default (legacy
    # non-rotating bot tokens). We default to a 1h horizon so consumers
    # can still call proactive_refresh predictably.
    assert before <= t.expires_at <= after + timedelta(hours=2)


def test_exchange_code_carries_user_token_when_user_scope_present():
    """When user_scope was requested, ``authed_user.access_token`` is the
    xoxp- token. The provider stashes it under ``provider_extras`` so
    consumers can read it without breaking the typed surface (bot is
    primary).
    """
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    routes = {
        "/api/oauth.v2.access": httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-BOT-T",
                "token_type": "bot",
                "scope": "channels:read",
                "bot_user_id": "U_BOT",
                "team": {"id": "T1"},
                "authed_user": {
                    "id": "U_AUTH",
                    "scope": "search:read",
                    "access_token": "xoxp-USER-T",
                },
            },
        )
    }
    p = SlackOAuthProvider(
        client_id="cid",
        client_secret="x",
        transport=_mock_transport(routes),
    )
    t = p.exchange_code(code="c", redirect_uri="https://cb")
    # Primary access_token is BOT.
    assert t.access_token == "xoxb-BOT-T"
    # User token (when present) lives in provider_extras under a stable key.
    assert t.provider_extras.get("user_access_token") == "xoxp-USER-T"
    assert t.provider_extras.get("authed_user_id") == "U_AUTH"
    assert t.provider_extras.get("team_id") == "T1"


def test_exchange_code_sends_required_form_params():
    """The token endpoint POST must include client_id, client_secret, code,
    redirect_uri per the OAuth 2.0 spec / Slack docs.
    """
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-X",
                "token_type": "bot",
                "scope": "channels:read",
                "team": {"id": "T1"},
                "authed_user": {"id": "U", "scope": "", "access_token": ""},
            },
        )

    p = SlackOAuthProvider(
        client_id="cid",
        client_secret="csec",
        transport=httpx.MockTransport(handler),
    )
    p.exchange_code(code="the-code", redirect_uri="https://cb")
    assert captured["method"] == "POST"
    assert "/api/oauth.v2.access" in captured["url"]
    form = parse_qs(captured["body"])
    assert form["code"] == ["the-code"]
    assert form["redirect_uri"] == ["https://cb"]
    assert form["client_id"] == ["cid"]
    assert form["client_secret"] == ["csec"]


def test_refresh_token_rotates_to_new_refresh_token():
    """Slack supports refresh-token rotation (workspaces with token rotation
    enabled). The new refresh_token replaces the old.
    """
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    routes = {
        "/api/oauth.v2.access": httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-NEW",
                "refresh_token": "xoxe-1-NEW-REFRESH",
                "token_type": "bot",
                "expires_in": 43200,
                "scope": "channels:read",
                "team": {"id": "T1"},
                "authed_user": {"id": "U", "scope": "", "access_token": ""},
            },
        )
    }
    p = SlackOAuthProvider(
        client_id="cid",
        client_secret="x",
        transport=_mock_transport(routes),
    )
    t = p.refresh_token("xoxe-1-OLD-REFRESH")
    assert t.access_token == "xoxb-NEW"
    # Slack rotates — new refresh_token wins.
    assert t.refresh_token == "xoxe-1-NEW-REFRESH"
    # 43200s = 12h expiry came back from server.
    assert t.expires_at > datetime.now(timezone.utc) + timedelta(hours=11)


def test_refresh_token_sends_grant_type_refresh_token():
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-X",
                "refresh_token": "xoxe-1-NEW",
                "token_type": "bot",
                "expires_in": 43200,
                "scope": "channels:read",
                "team": {"id": "T1"},
                "authed_user": {"id": "U", "scope": "", "access_token": ""},
            },
        )

    p = SlackOAuthProvider(
        client_id="cid",
        client_secret="csec",
        transport=httpx.MockTransport(handler),
    )
    p.refresh_token("xoxe-1-OLD")
    form = parse_qs(captured["body"])
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["xoxe-1-OLD"]
    assert form["client_id"] == ["cid"]
    assert form["client_secret"] == ["csec"]


def test_validate_scopes_subset():
    from chunkshop.oauth import OAuthTokens
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    p = SlackOAuthProvider(client_id="cid", client_secret="x")
    tok = OAuthTokens(
        access_token="xoxb-X",
        refresh_token=None,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes=["channels:history", "channels:read", "users:read"],
        provider="slack",
        provider_extras={},
    )
    assert p.validate_scopes(tok, ["channels:history"]) is True
    assert p.validate_scopes(
        tok, ["channels:history", "channels:read"]
    ) is True
    assert p.validate_scopes(tok, []) is True
    # Missing scope → False
    assert p.validate_scopes(tok, ["chat:write"]) is False
    assert p.validate_scopes(tok, ["channels:read", "chat:write"]) is False


def test_exchange_code_raises_on_slack_ok_false():
    """Slack returns HTTP 200 with ``ok: false`` on bad codes — we surface
    that as an exception so consumers don't get a silent half-success.
    """
    from chunkshop_connectors.oauth.slack import SlackOAuthProvider

    routes = {
        "/api/oauth.v2.access": httpx.Response(
            200,
            json={"ok": False, "error": "invalid_code"},
        )
    }
    p = SlackOAuthProvider(
        client_id="cid",
        client_secret="x",
        transport=_mock_transport(routes),
    )
    with pytest.raises(Exception, match="invalid_code"):
        p.exchange_code(code="bad", redirect_uri="https://cb")
