# Top

Preamble paragraph that comes before any second-level heading. Python's heading_boundary framer with `pattern: '^##\s'` should treat this whole stretch (including the `# Top` line) as the preamble frame.

## Section A

Body of section A. Two short sentences here keep things tidy.

## Section B

Body of section B. Slightly different so the framed docs are distinguishable.

### Section B.1

Subsection that the `^##\s` pattern does NOT split on (only matches exactly two `#`s). It's part of section B's body.

## Section C

Body of section C. Last section in the corpus.
