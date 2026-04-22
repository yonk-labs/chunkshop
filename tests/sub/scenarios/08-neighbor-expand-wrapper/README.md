# 08 — neighbor_expand wraps sentence_aware

Exercises chunker composition: the `neighbor_expand` chunker wraps a
`sentence_aware` base chunker with `window: 1`, so each chunk's
`embedded_content` sees the previous and next chunk concatenated in. The
`original_content` (what you grep) stays as just the target chunk — proves the
two-text-field contract (original vs embedded) holds under wrapping. Uses a
tiny json_corpus of 4 related paragraphs so neighbor relationships are visible.
