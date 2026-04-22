# 03 — heading-boundary framer

Exercises the `heading_boundary` framer: one source document (a monolithic
markdown blob with four `## Topic` sections) gets split into four framed
sub-documents BEFORE chunking. `title_from_heading: true` lifts the heading
text into each frame's title. Useful pattern for ingesting a wiki export, a
changelog, or any single-file multi-topic document.
