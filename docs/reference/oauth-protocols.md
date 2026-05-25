# OAuth Protocols + helpers

**Module**: `chunkshop.oauth`
**Type**: Utility — Protocol contracts + reusable helpers
**Ship status**: verified
**Optional extra**: none (stdlib only)
**Since**: extended 2026-05-25 (review fix `b1f218a`, `3fda319`)

## Purpose

Vendor-agnostic OAuth surface that connectors consume. Defines two
`Protocol` types (`OAuthProvider`, `OAuthTokenStorage`) plus the
`OAuthTokens` dataclass and the `proactive_refresh` helper. Vendor
providers (Google, Slack, Microsoft, …) live in
`chunkshop_connectors.oauth.<vendor>`.

Implementors building a new OAuth provider use this module's contracts.

## Public API

```python
from chunkshop.oauth import (
    OAuthTokens,
    OAuthProvider,
    OAuthTokenStorage,
    proactive_refresh,
    MockOAuthProvider,
)
```

### `OAuthTokens` (dataclass)

```python
@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: list[str]
    provider: str
    provider_extras: dict
```

`__repr__` redacts `access_token` and `refresh_token` so a stray
`log.debug(tokens)` doesn't leak credentials.

### `OAuthProvider` (Protocol)

```python
@runtime_checkable
class OAuthProvider(Protocol):
    def authorization_url(
        self, state: str, redirect_uri: str, scopes: list[str]
    ) -> str: ...
    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...
    def refresh_token(self, refresh_token: str) -> OAuthTokens: ...
    def validate_scopes(self, tokens: OAuthTokens, required: list[str]) -> bool: ...
```

Implementing this Protocol = your class can be plugged into any
chunkshop connector that takes an `OAuthProvider`.

### `OAuthTokenStorage` (Protocol)

```python
@runtime_checkable
class OAuthTokenStorage(Protocol):
    async def get(self, user_id: str, provider: str) -> Optional[OAuthTokens]: ...
    async def put(self, user_id: str, provider: str, tokens: OAuthTokens) -> None: ...
    async def delete(self, user_id: str, provider: str) -> None: ...
```

Interface only — **chunkshop never persists tokens**. Storage is
tenancy-scoped (per-user, per-app, per-vault) so consumers own the
impl: a Postgres table, HashiCorp Vault, AWS KMS, etc.

### `proactive_refresh(tokens, *, provider, leeway_minutes=5)`

```python
def proactive_refresh(
    tokens: OAuthTokens,
    *,
    provider: OAuthProvider,
    leeway_minutes: int = 5,
) -> Optional[OAuthTokens]: ...
```

Returns refreshed tokens if `expires_at - now <= leeway_minutes`, else
`None`. Avoids the reactive-401 refresh race where two concurrent
callers both see a 401 and both try to refresh, one losing its refresh
token.

**Naive `expires_at`** (no tzinfo) is interpreted as UTC. Common when
tokens round-trip through JSON storage that strips tzinfo. This was
hardened in the SP-1 review (commit `3fda319`) — naive datetimes used
to crash the subtraction.

### `MockOAuthProvider`

```python
class MockOAuthProvider:
    def __init__(self): ...
    # Issues monotonically-numbered tokens; each call to exchange_code /
    # refresh_token yields tokens with a fresh suffix so tests can
    # observe refreshes.
```

Importable via `from chunkshop.oauth import MockOAuthProvider`. Used in
tests to exercise refresh flows without network. There's also a
pytest fixture:

```python
# in your conftest.py
pytest_plugins = ["chunkshop.testing.fixtures"]

# then in your test:
def test_my_connector(mock_oauth_provider):
    ...
```

## Behavior contract

1. **No persistence in core.** chunkshop core never writes tokens to
   disk / DB / anywhere. The consumer's storage layer
   (`OAuthTokenStorage` impl) is responsible.
2. **`__repr__` always redacts.** Any code path that prints / logs
   tokens prints `<redacted>` for the secret fields.
3. **Naive-datetime tolerance** in `proactive_refresh` — JSON-serialized
   tokens often lose tzinfo, that's fine.
4. **`OAuthProvider` is `runtime_checkable`.** `isinstance(x,
   OAuthProvider)` works to gate consumer code by Protocol
   conformance.
5. **`MockOAuthProvider`'s issued tokens have a monotonic
   `provider_extras["n"]`** so tests can detect "did we actually
   refresh or did we re-use the cached one?".

## Inputs / Outputs

Inputs and outputs are entirely defined by the provider implementation
(varies by vendor). The Protocol just locks the method signatures.

## Errors

| Exception | When |
|-----------|------|
| `httpx.HTTPStatusError` | (provider-specific) Vendor's token endpoint returned non-2xx. |
| Custom per-impl | Up to the provider. |

## Example: build your own provider

```python
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx
from chunkshop.oauth.tokens import OAuthTokens

class GitHubOAuthProvider:
    """Implements OAuthProvider — github OAuth uses a different shape than google."""

    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(timeout=30.0)

    def authorization_url(self, state, redirect_uri, scopes):
        return "https://github.com/login/oauth/authorize?" + urlencode({
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(scopes),
        })

    def exchange_code(self, code, redirect_uri):
        resp = self._http.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        d = resp.json()
        return OAuthTokens(
            access_token=d["access_token"],
            refresh_token=d.get("refresh_token"),
            # GitHub PATs don't expire; access_tokens via OAuth do not have
            # a uniform expires_in. Use a long sentinel:
            expires_at=datetime.now(timezone.utc) + timedelta(days=365),
            scopes=d.get("scope", "").split(","),
            provider="github",
            provider_extras={},
        )

    def refresh_token(self, refresh_token):
        raise NotImplementedError("github OAuth tokens don't rotate this way")

    def validate_scopes(self, tokens, required):
        return set(required).issubset(set(tokens.scopes))
```

`isinstance(GitHubOAuthProvider("a", "b"), OAuthProvider)` returns
True — Protocol conformance is structural.

## Example: proactive refresh wrapper

```python
from chunkshop.oauth import proactive_refresh

def get_fresh_tokens(provider, stored):
    fresh = proactive_refresh(stored, provider=provider, leeway_minutes=5)
    if fresh is not None:
        # tokens were near-expiry → got new ones
        save_to_storage(fresh)
        return fresh
    return stored
```

## Tests proving the contract

- `tests/chunkshop/test_oauth_*`:
  - `OAuthTokens.__repr__` redacts both token fields
  - `proactive_refresh` returns None when `now < expires - leeway`
  - `proactive_refresh` returns fresh tokens when within leeway
  - `proactive_refresh` handles naive `expires_at` as UTC (regression
    test for commit `3fda319`)
  - `MockOAuthProvider` issues monotonic tokens
  - `isinstance(MockOAuthProvider(), OAuthProvider)` passes (Protocol
    structural conformance)

## See also

- Reference: [`oauth-google`](oauth-google.md) — concrete provider
- Reference: [`source-gdrive`](source-gdrive.md) — connector consuming OAuth tokens
- Reference: [`utility-testing`](utility-testing.md) — `mock_oauth_provider` pytest fixture
