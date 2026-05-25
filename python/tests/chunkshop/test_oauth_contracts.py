# tests/chunkshop/test_oauth_contracts.py
from datetime import datetime, timedelta, timezone
from chunkshop.oauth import OAuthTokens, OAuthProvider, OAuthTokenStorage


def test_tokens_dataclass():
    t = OAuthTokens(access_token="a", refresh_token="r",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    scopes=["read"], provider="google", provider_extras={})
    assert t.provider == "google"
    assert "read" in t.scopes


class _Prov:
    def authorization_url(self, state, redirect_uri, scopes): return "https://x"
    def exchange_code(self, code, redirect_uri): ...
    def refresh_token(self, refresh_token): ...
    def validate_scopes(self, tokens, required): return True


class _Store:
    async def get(self, user_id, provider): ...
    async def put(self, user_id, provider, tokens): ...
    async def delete(self, user_id, provider): ...


def test_provider_and_storage_runtime_checkable():
    assert isinstance(_Prov(), OAuthProvider)
    assert isinstance(_Store(), OAuthTokenStorage)


def test_tokens_repr_redacts_secrets():
    """Naive `log.debug(tokens)` in consumer code must not leak credentials."""
    t = OAuthTokens(access_token="ya29.SECRET",
                    refresh_token="1//SECRET_REFRESH",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    scopes=["read"], provider="google", provider_extras={})
    r = repr(t)
    assert "ya29.SECRET" not in r
    assert "SECRET_REFRESH" not in r
    assert "<redacted>" in r
    # Non-secret fields still visible for debugging.
    assert "google" in r
    assert "read" in r


def test_tokens_repr_when_refresh_token_none():
    t = OAuthTokens(access_token="ya29.SECRET", refresh_token=None,
                    expires_at=datetime.now(timezone.utc),
                    scopes=[], provider="anon", provider_extras={})
    r = repr(t)
    assert "ya29.SECRET" not in r
    assert "refresh_token=None" in r
