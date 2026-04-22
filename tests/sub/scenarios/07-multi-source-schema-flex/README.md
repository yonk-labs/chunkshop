# 07 — multi-source schema-flex (create_if_missing + append)

Two cells write to the SAME table with different `source_tag`s, proving the
schema-flex path works end-to-end:
- `config-a.yaml` — markdown source, `mode: create_if_missing`, `source_tag: docs_markdown` (creates the table).
- `config-b.yaml` — json_corpus source, `mode: append`, `source_tag: api_items` (layers in on top).

After both cells run, the single table `unified_multi_source` contains rows
from both sources, discriminable by the `source` column. This is the reference
pattern for heterogeneous corpora that need to live in one pgvector table.
`run-all.sh` runs the two configs in lexical order (a before b).
