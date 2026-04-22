# 05 — composite extractor + lang_detect + promote_metadata

Exercises four features together:
- `json_corpus` source with 4 docs in different languages (en, fr, de, es).
- `composite` extractor chaining `lang_detect` + `rake_keywords`.
- `promote_metadata` lifting `language` from metadata JSONB into a typed column.
- schema-flex `mode: overwrite` with `source_tag`.

Proves the composite extractor runs both stages, that lang_detect tags each chunk
with its ISO-639-1 code, and that the promoted `language` column is populated.
