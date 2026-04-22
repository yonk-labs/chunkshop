# Authenticating with the v2 API

All v2 endpoints require a bearer token. Tokens are scoped to a single tenant and a single API key; losing the key means rotating it, not recovering it — we never show a key a second time.

## Creating an API key

API keys are created from Admin → API Keys. On creation, you choose a scope set (`read`, `write`, `admin`) and an optional IP allowlist. The response shows the key once; copy it into your secret manager before closing the dialog. If you navigate away without copying, create a new key and delete the old one — we cannot retrieve it from storage.

Keys are random 48-byte strings, base64url-encoded, prefixed `sk_live_` for production and `sk_test_` for sandbox tenants. Prefix inspection is a safe way for client code to log which environment a request targeted without leaking the secret.

## Using a key

Send the key in the `Authorization` header as `Bearer <key>`. We reject tokens in query strings on write endpoints — they end up in access logs, HTTP referrers, and browser history, and that is a category of leak we are not willing to underwrite.

```http
POST /v2/orders HTTP/1.1
Host: api.example.com
Authorization: Bearer sk_live_abc123...
Content-Type: application/json

{"customer_id": "cus_42", "total": 9900}
```

## Rotation and revocation

From the same page, click "Rotate" to issue a new key and mark the old one for 24-hour sunset — both keys work during the window so you can push your config update. Click "Revoke" to kill a key immediately; revoked keys return `401 Unauthorized` with `code: "revoked_key"`. We recommend rotating on a quarterly cadence and immediately if a key is suspected of leaking.

## Error responses

Bearer token problems surface as `401 Unauthorized` with a structured body:

```json
{
  "code": "invalid_key" | "expired_key" | "revoked_key" | "ip_not_allowed",
  "message": "<human readable explanation>"
}
```

The `code` field is stable; the `message` field is for humans and may change between releases.
