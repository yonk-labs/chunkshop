# Quickstart: two sources → one table

Minimum YAML diff from the default `sample.yaml` to enable multi-source ingest.

## The change

```diff
 target:
   dsn_env: CHUNKSHOP_DSN
   schema: mydata
   table: all_docs
-  overwrite: true
+  mode: create_if_missing        # first cell; `append` for later cells
+  source_tag: pdfs_q2_2026       # required when mode=append
+  promote_metadata:              # optional — lifts jsonb paths to typed cols
+    - path: language
+      type: text
```

## Run two cells

```bash
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"

# First cell creates the table:
chunkshop ingest --config cell-a.yaml      # mode: create_if_missing

# Second cell appends — pre-flight verifies dim match + schema compat:
chunkshop ingest --config cell-b.yaml      # mode: append
```

## Verify

```sql
\c mydb
SELECT source, COUNT(*) FROM mydata.all_docs GROUP BY source;
-- Two source_tag values, non-zero counts each.
\d mydata.all_docs
-- Columns include: source text, language text (if promoted), plus chunkshop defaults.
```

## Cheatsheet

| Want to…                                              | Set                                                                     |
|-------------------------------------------------------|-------------------------------------------------------------------------|
| Create the table                                      | `mode: create_if_missing`                                               |
| Add rows to an existing table                         | `mode: append` + `source_tag: <lowercase_ident>`                        |
| Drop and recreate (same cell as before)               | `mode: overwrite` (default when no `source_tag` conflict)               |
| Drop and recreate ignoring foreign source_tag rows    | `mode: overwrite` + `force_overwrite: true`                             |
| Promote a metadata path to a typed column             | `promote_metadata: [{path: entities.ORG, type: "text[]"}]`              |

Allowed `promote_metadata.type` values: `text`, `text[]`, `int`, `bigint`, `boolean`, `jsonb`, `timestamptz`, `date`.

Full walkthrough: [`tutorial-multi-source.md`](tutorial-multi-source.md).
