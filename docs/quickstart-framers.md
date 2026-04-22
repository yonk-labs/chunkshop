# Quickstart: DocFramer recipes

Pick a framer from the decision tree, paste the YAML, run `chunkshop ingest`. For the
full walkthrough with verification and retrieval queries, see
[`tutorial-framers.md`](tutorial-framers.md).

## Decision tree

| Your source gives you…                                  | Use                                                |
|---------------------------------------------------------|----------------------------------------------------|
| One doc per row, already split correctly                | Nothing — default `IdentityFramer` (omit `framer:`) |
| Markdown with `##` sections as logical docs             | `heading_boundary` with `pattern: '^##\s'`         |
| Plain text with a custom separator (e.g. "About X...")  | `regex_boundary` with your pattern                 |
| JSON with docs nested under `items[*]` (or deeper)      | `jsonpath` with `row_path: items.*`                |

## YAML recipes

### `heading_boundary` — split markdown on a heading pattern

```yaml
framer:
  type: heading_boundary
  pattern: '^##\s'           # match every line starting with "## "
  title_from_heading: true   # use the heading text as the framed doc's title
```

Preamble above the first matching heading becomes frame 0 if non-empty. Each heading
starts a new frame.

### `regex_boundary` — split on an arbitrary regex

```yaml
framer:
  type: regex_boundary
  split_pattern: '(?:^|(?<=[.?!]\s))About\s+'   # medical-topic boundary
  title_pattern: 'About\s+([^.?]{3,80})'        # first capture group = title
  body_starts_with_match: true                  # include "About ..." in the body
```

Every match of `split_pattern` starts a new frame. If `title_pattern` is provided, its
first capture group on the frame body becomes the framed doc's title. Empty slices are
dropped.

### `jsonpath` — expand a nested JSON array into N frames

```yaml
framer:
  type: jsonpath
  row_path: items.*      # iterate every element of items[]
  title_path: title      # per-row title field
  body_path: body        # per-row body field (the chunker's input)
```

Parses `raw.content` as JSON, walks `row_path` (`*` = iterate a list), and emits one
framed doc per element. Combine with `type: files` on the source — `json_corpus`
already iterates rows on its own.

## What the framer writes to metadata

Every framed doc gets two metadata keys, stamped by every framer:

- `metadata.framer` — the framer name (`'heading_boundary'`, `'regex_boundary'`,
  `'jsonpath'`, or `'identity'`).
- `metadata.frame_seq` — a 0-indexed integer per raw source doc.

These propagate through the chunker to every row in the target table. If you want to
filter or index on them at the SQL level, promote them via `target.promote_metadata`:

```yaml
target:
  promote_metadata:
    - path: framer
      type: text
    - path: frame_seq
      type: int
```

## Gotchas

- **Framer runs BEFORE chunker.** If your framer emits 20 framed docs and the chunker
  produces 5 chunks per frame, you write 100 rows per raw source doc. Budget for this
  when sizing the table or picking HNSW vs. seq scan.
- **IDs get `#<frame_seq>` appended.** With `id_from: stem`, a file `handbook.md`
  yielding 4 frames produces doc IDs `handbook#0`, `handbook#1`, `handbook#2`,
  `handbook#3`. `IdentityFramer` leaves the raw ID alone (no `#` suffix).
- **Regex patterns are validated at config load.** An invalid `split_pattern` or
  `title_pattern` fails with a pydantic error before any ingest work happens — you
  won't discover it mid-run.
- **`jsonpath` paths are allowlisted.** The validator requires
  `^[a-z_0-9][a-z_0-9.*]*$` (plus literal `$` for root). Uppercase JSON keys are not
  supported in the current implementation — if your payload uses `CamelCase` keys you
  need to lowercase them upstream or use a different framer.
- **Pick the stage that iterates.** A `json_corpus` source already expands rows. Don't
  stack `jsonpath` on top of it — use `type: files` so the whole JSON blob lands in
  `raw.content` as one string and let the framer iterate.

## Full walkthrough

End-to-end scenarios with SQL verification, retrieval examples, and the before/after
comparison to bespoke Python splitters: [`tutorial-framers.md`](tutorial-framers.md).
