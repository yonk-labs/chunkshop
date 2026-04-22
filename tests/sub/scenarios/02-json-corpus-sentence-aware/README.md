# 02 — json corpus, sentence-aware chunker, rake keywords

Exercises three features together: `json_corpus` source (reading a single JSON
file with a `documents` array), the `sentence_aware` prose chunker, and the
`rake_keywords` extractor stamping RAKE-derived key phrases into each chunk's
metadata. Useful as a template for anyone ingesting a JSON document dump that
already has clean `{id, title, content}` records.
