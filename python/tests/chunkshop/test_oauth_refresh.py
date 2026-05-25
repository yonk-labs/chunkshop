# tests/chunkshop/test_oauth_refresh.py
from datetime import datetime, timedelta, timezone
from chunkshop.oauth import OAuthTokens, MockOAuthProvider, proactive_refresh


def _tok(minutes):
    return OAuthTokens(access_token="a", refresh_token="r",
                       expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
                       scopes=["read"], provider="mock", provider_extras={})


def test_refresh_when_within_leeway():
    prov = MockOAuthProvider()
    out = proactive_refresh(_tok(2), provider=prov, leeway_minutes=5)
    assert out is not None
    assert out.access_token != "a"  # mock issues a fresh token


def test_no_refresh_when_outside_leeway():
    prov = MockOAuthProvider()
    assert proactive_refresh(_tok(60), provider=prov, leeway_minutes=5) is None


def test_mock_provider_predictable_tokens():
    prov = MockOAuthProvider()
    t = prov.exchange_code("code", "https://cb")
    assert t.provider == "mock"
    assert t.access_token.startswith("mock-access-")
