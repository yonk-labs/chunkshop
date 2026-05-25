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
