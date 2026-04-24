# 11 — `hierarchical_summary`: fine + coarse rows linked by `group_id`

Exercises the full hierarchical-retrieval pattern: emit base `hierarchy`
chunks (the fine rows, one per `#` section) **plus** coarse summary rows
(one per section, grouped by `strategy: section_aware`), all in the same
table, linked by `metadata.group_id`.

- **Source:** one markdown file with three `#` sections.
- **Base chunker:** `hierarchy` (required for `section_aware` grouping).
- **Summarizer:** `passthrough` — keeps CI fast + dep-free; in production
  you'd wire skimr or sumy here.
- **Promote:** `metadata.granularity` → `text` and `metadata.group_id` → `text`
  so retrieval can filter `WHERE granularity = 'fine'` with a GIN index.

Expected shape:
- 3 base ("fine") rows — one per `#` heading.
- 3 coarse summary rows — one per group.
- Total 6 rows; each row has `granularity ∈ {fine, coarse}` and a
  non-null `group_id`.

Enables the match-coarse / return-fine query pattern — match on a
coarse summary, then pull the full fine chunks in that `group_id`.
