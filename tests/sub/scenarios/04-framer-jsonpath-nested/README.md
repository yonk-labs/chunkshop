# 04 — JSONPath framer on nested JSON

Exercises the `jsonpath` framer: a single JSON file shaped `{items: [...]}` is
walked with `row_path: items.*`, producing one framed document per item. Titles
come from `title_path: title`, bodies from `body_path: body`. This is the
pattern to use when you have a JSON dump whose shape doesn't match the
`json_corpus` source's `{documents: [...]}` convention — load the whole file as
a single `files` source doc, then let the framer fan it out.
