# 01 — markdown hierarchy default

Exercises the canonical default path: `files` source with markdown globs,
`hierarchy` chunker (the shipped default), no framer, no extractor. Confirms
three short markdown docs with H1/H2 sections split into multiple chunks and
that `metadata.strategy` + `metadata.heading` are populated — the baseline
guarantee everything else builds on.
