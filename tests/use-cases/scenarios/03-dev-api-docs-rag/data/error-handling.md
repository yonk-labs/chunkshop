# Error handling across the v2 API

Every error response has the same shape: an HTTP status code, a stable machine-readable `code`, and a human-readable `message`. Client code branches on `code`; `message` is for logs and developer surfaces only.

## Response shape

```json
{
  "error": {
    "code": "resource_not_found",
    "message": "No order matched the provided id.",
    "request_id": "req_01HQZT8G9AVX...",
    "details": { }
  }
}
```

The `request_id` is how we find the request in our logs when you open a support ticket. Include it in every bug report. The `details` object is endpoint-specific — validation errors list the offending field, rate-limit errors include a retry timestamp.

## The canonical error code list

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `validation_failed` | Request body failed schema validation. `details.field_errors` enumerates fields. |
| 400 | `invalid_cursor` | Pagination cursor was malformed or used against a changed filter set. |
| 401 | `invalid_key` | Token does not match any active key. |
| 401 | `expired_key` | Token existed but its TTL has elapsed. |
| 401 | `revoked_key` | Token was explicitly revoked in Admin. |
| 403 | `insufficient_scope` | Token lacks the scope required for this endpoint. |
| 404 | `resource_not_found` | Resource id did not match any row. |
| 409 | `idempotency_conflict` | Same `Idempotency-Key` used with a different body. |
| 410 | `cursor_expired` | Cursor older than seven days. Restart the walk. |
| 429 | `rate_limited` | Over quota. `details.retry_after` is seconds. |
| 500 | `internal_error` | We broke. `request_id` is mandatory for the ticket. |
| 503 | `service_unavailable` | Planned maintenance or partial outage. Retry with backoff. |

## Idempotency

All write endpoints accept an `Idempotency-Key` header. We store the first response for 24 hours and return it verbatim for any repeat of the same key — this includes HTTP status and body. Different body + same key returns `409 idempotency_conflict`; same body + same key returns the cached response even if the underlying resource has since changed.

We recommend a UUIDv4 per logical intent — not per retry — so automatic retries in your HTTP client converge on the same server-side result.

## Retry policy we recommend

- `500` and `503` — retry with exponential backoff (1s, 2s, 4s, 8s, cap at 30s). Use jitter.
- `429` — honor `details.retry_after`.
- `400`, `401`, `403`, `404`, `409` — do not retry. These are bugs in the caller, not transient failures, and retrying hides the real cause.
