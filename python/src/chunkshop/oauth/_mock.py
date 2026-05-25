# src/chunkshop/oauth/_mock.py
from __future__ import annotations
import itertools
from datetime import datetime, timedelta, timezone
from chunkshop.oauth.tokens import OAuthTokens


class MockOAuthProvider:
    """Predictable provider for tests — no network. Each issued token has a
    monotonically increasing suffix so refreshes are observably different."""
    def __init__(self):
        self._counter = itertools.count(1)

    def _issue(self) -> OAuthTokens:
        n = next(self._counter)
        return OAuthTokens(
            access_token=f"mock-access-{n}",
            refresh_token=f"mock-refresh-{n}",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=["read"], provider="mock", provider_extras={"n": n})

    def authorization_url(self, state, redirect_uri, scopes):
        return f"https://mock/auth?state={state}"

    def exchange_code(self, code, redirect_uri):
        return self._issue()

    def refresh_token(self, refresh_token):
        return self._issue()

    def validate_scopes(self, tokens, required):
        return set(required).issubset(set(tokens.scopes))
