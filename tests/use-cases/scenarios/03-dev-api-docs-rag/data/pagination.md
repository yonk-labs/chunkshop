# Paginating list endpoints

Every v2 list endpoint returns a cursor-paginated response. We moved away from offset/limit in v1 because offset pagination re-reads the full result window on every page — a query that walks 100k rows ends up scanning millions of rows total. Cursors avoid that by anchoring on an indexed column and reading forward.

## Response shape

Every paginated response has this shape:

```json
{
  "data": [ ... ],
  "has_more": true,
  "next_cursor": "eyJpZCI6InN0cl80MiIsInRzIjoiMjAyNC0wMy0xNFQxMjowMDowMFoifQ=="
}
```

When `has_more` is `false`, `next_cursor` is null and you have reached the end of the result set. Do not attempt to use a null cursor — the server returns `400 Bad Request` with `code: "invalid_cursor"`.

## Requesting the next page

Pass the cursor back as the `cursor` query parameter. Page size is controlled by `limit` (default 25, max 100) and stays constant across pages — do not change `limit` mid-walk or the cursor becomes invalid and you get a `400`.

```bash
curl -H "Authorization: Bearer $KEY" \
  "https://api.example.com/v2/orders?limit=50"

# Next page
curl -H "Authorization: Bearer $KEY" \
  "https://api.example.com/v2/orders?limit=50&cursor=eyJpZCI6InN0cl80MiIsInRzIjoiMjAyNC0wMy0xNFQxMjowMDowMFoifQ=="
```

## Filters combine with pagination

All list endpoints accept the same filter parameters regardless of pagination state. Filters are evaluated against the full dataset and the cursor respects them — a filtered walk visits only matching rows. Adding or removing a filter between pages invalidates the cursor; start over with a fresh first-page request.

## Cursor stability

Cursors are stable for seven days from issue. After that the server returns `410 Gone` and you must restart the walk. This matters for batch jobs — if your overnight export takes longer than a week (it probably should not), split it into windowed runs rather than a single paginated crawl.

Cursors are opaque. Do not parse or modify them; the structure will change between API versions without notice and we publish no schema.
