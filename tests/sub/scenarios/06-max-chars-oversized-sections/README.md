# 06 — hierarchy max_chars splits oversized sections

Exercises the hierarchy chunker's `max_chars` safety valve: one markdown file
has a single H1 followed by ~10 KB of prose with no further headings. Without
a split limit the whole body would be one chunk, exceeding typical embedder
context windows. The chunker detects the oversize and splits the section into
smaller parts, recording the split index in `metadata.section_part`. Confirms
the default `max_chars: 2000` is load-bearing, not cosmetic.
