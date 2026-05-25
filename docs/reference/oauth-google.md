# `GoogleOAuthProvider`

**Module**: `chunkshop_connectors.oauth.google`
**Type**: OAuth provider
**Ship status**: verified
**Optional extra**: `chunkshop-connectors[gdrive]` (httpx)
**Since**: 2026-05-25 (commit `46b8517`)

## Purpose

Concrete `OAuthProvider` (Protocol) implementation for Google's OAuth
2.0 endpoints (Drive, Gmail, Calendar, …). Only depends on `httpx` —
no `google-auth` / `google-api-python-client`, both of which are heavy
and awkward to mock hermetically.

Codifies three Google-specific quirks so consumers can't accidentally
miss them:

- `access_type=offline` on the consent URL → required for refresh tokens.
- `prompt=consent` on the consent URL → required to re-issue refresh
  tokens on repeat auths.
- Preserves the *caller-supplied* refresh_token on
  `grant_type=refresh_token` when Google's response omits one (it
  often does — Google only rotates the refresh_token under specific
  conditions).

## Public API

```python
from chunkshop_connectors.oauth.google import GoogleOAuthProvider
from chunkshop.oauth.tokens import OAuthTokens

class GoogleOAuthProvider:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        token_endpoint: str = "https://oauth2.googleapis.com/token",
        auth_endpoint: str = "https://accounts.google.com/o/oauth2/v2/auth",
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 30.0,
    ) -> None: ...

    # OAuthProvider protocol
    def authorization_url(
        self, state: str, redirect_uri: str, scopes: list[str]
    ) -> str: ...
    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...
    def refresh_token(self, refresh_token: str) -> OAuthTokens: ...
    def validate_scopes(self, tokens: OAuthTokens, required: list[str]) -> bool: ...
```

## Behavior contract

1. **Implements `chunkshop.oauth.OAuthProvider`** Protocol — see
   [`oauth-protocols`](oauth-protocols.md) for the contract.
2. **`authorization_url`** builds:
   ```
   https://accounts.google.com/o/oauth2/v2/auth?
       response_type=code
       &client_id=<id>
       &redirect_uri=<uri>
       &scope=<space-separated scopes>
       &state=<state>
       &access_type=offline
       &prompt=consent
   ```
   The `access_type=offline` + `prompt=consent` pair is non-negotiable
   for offline / long-lived access.
3. **`exchange_code`** POSTs to the token endpoint with
   `grant_type=authorization_code`. Builds `OAuthTokens` with
   `provider="google"` and stuffs any non-standard fields into
   `provider_extras`.
4. **`refresh_token`** POSTs with `grant_type=refresh_token`. If
   Google's response omits `refresh_token`, the provider re-uses the
   caller-supplied one so offline access keeps working.
5. **`validate_scopes`** is set-difference: `required ⊆ tokens.scopes`.
6. **`expires_at`** computed as `datetime.now(UTC) +
   timedelta(seconds=payload["expires_in"])`, defaulting to 3600s if
   the payload omits it.
7. **`__repr__` redacts `client_secret`** so a stray
   `print(provider)` doesn't leak it.
8. **`transport=` kwarg** is a hermetic test hook
   (`httpx.MockTransport`); production leaves it None.
9. **Raises `httpx.HTTPStatusError`** on any non-2xx from Google's
   token endpoint. The caller is responsible for surfacing the error
   body (Google's responses include actionable hints like the API
   activation URL on 403).

## Inputs

- Google Cloud OAuth client ID + secret (Desktop or Web app type).
- Authorization code (from the consent redirect).
- Scopes (e.g. `https://www.googleapis.com/auth/drive.readonly`).

## Outputs

- `OAuthTokens` dataclass with `access_token`, `refresh_token`,
  `expires_at`, `scopes`, `provider="google"`, and `provider_extras`
  carrying any extra fields (`token_type`, `id_token`, etc.).

## Errors

| Exception | When |
|-----------|------|
| `httpx.HTTPStatusError` | Non-2xx from Google's token endpoint. |
| `KeyError` | `payload["access_token"]` missing — shouldn't happen for valid Google responses. |

## Example: bootstrap flow with a loopback callback

```python
import secrets, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from chunkshop_connectors.oauth.google import GoogleOAuthProvider
from chunkshop.oauth.refresh import proactive_refresh

provider = GoogleOAuthProvider(
    client_id="<your-client-id>.apps.googleusercontent.com",
    client_secret="GOCSPX-…",
)
state = secrets.token_urlsafe(16)
redirect_uri = "http://localhost:8765/callback"
url = provider.authorization_url(
    state=state,
    redirect_uri=redirect_uri,
    scopes=["https://www.googleapis.com/auth/drive.readonly"],
)
webbrowser.open(url)

# Spin up a one-shot HTTP server on :8765 to catch the redirect, parse the
# ?code=... param, then:
tokens = provider.exchange_code(code=received_code, redirect_uri=redirect_uri)
# tokens.access_token / .refresh_token / .expires_at populated

# Proactive refresh (avoid 401 races):
fresh = proactive_refresh(tokens, provider=provider, leeway_minutes=5)
if fresh:
    tokens = fresh

# Persist tokens.access_token + refresh_token + expires_at + scopes for
# the gdrive connector's GDRIVE_OAUTH_TOKENS env var.
```

A full working example is at `python/connectors/examples/e2e_gdrive_real_flow.py`.

## Example: hermetic test

```python
import httpx
from chunkshop_connectors.oauth.google import GoogleOAuthProvider

def fake_handler(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "access_token": "ya29.fake", "refresh_token": "1//fake",
        "expires_in": 3600, "scope": "https://example/scope",
    })

provider = GoogleOAuthProvider(
    "id", "secret",
    transport=httpx.MockTransport(fake_handler),
)
tokens = provider.exchange_code(code="ignored", redirect_uri="ignored")
assert tokens.refresh_token == "1//fake"
```

## How it integrates with the pipeline

The Google OAuth provider is used to mint the `oauth_tokens` dict that
the [`gdrive`](source-gdrive.md) connector consumes. The
`chunkshop.oauth.proactive_refresh` helper (see
[`oauth-protocols`](oauth-protocols.md)) wraps this provider with a
race-safe refresh path.

Token persistence is the consumer's responsibility. `chunkshop.oauth.OAuthTokenStorage`
is a Protocol — there's no built-in storage. The
`e2e_gdrive_real_flow.py` demo caches tokens at
`~/.chunkshop/gdrive-tokens.json` as a reference but that's not
production-grade.

## Tests proving the contract

- `python/connectors/tests/test_google_oauth_provider.py`:
  - `authorization_url` includes `access_type=offline` + `prompt=consent`
  - `exchange_code` round-trip via `MockTransport`
  - `refresh_token` preserves the caller's refresh_token when response
    omits it
  - `validate_scopes` truthy / falsy cases
  - `__repr__` redacts the client secret
- Live demo: `python/connectors/examples/e2e_gdrive_real_flow.py`.

## See also

- Reference: [`oauth-protocols`](oauth-protocols.md) — the Protocol
  contract this provider implements
- Reference: [`source-gdrive`](source-gdrive.md) — the connector that
  consumes Google OAuth tokens
- [`docs/connectors/gdrive.md`](../connectors/gdrive.md) — Google Cloud
  Console setup walkthrough
