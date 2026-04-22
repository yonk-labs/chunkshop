# API rate limits and the 429 response

Every tenant gets 600 requests per minute on v2 endpoints by default, measured on a rolling 60-second window per API key. Over the limit and we return `429 Too Many Requests` with a `Retry-After` header in seconds.

## Which endpoints count

All `/v2/*` endpoints count against the same bucket. Webhook deliveries do not — those are outbound from us and run on a separate queue. The `/health` endpoint also does not count, so monitoring tools can hit it without eating into the quota.

## Raising the limit

Self-serve up to 2000 req/min via Admin → API Keys → (key) → Rate limit. Above that we need a support ticket — we check whether the workload actually needs the headroom or whether pagination or batching would solve the same problem without a limit bump.

## Handling 429 in client code

Respect `Retry-After`. Exponential backoff without it tends to compound the problem — if every client backs off the same amount, they all retry at the same moment and trigger a second wave of 429s.
