# if_oversize fallback chain — runnable demo

Demonstrates the 0.3.2 `if_oversize` field. Same corpus, two cells:

- `no-fallback.yaml`: `neighbor_expand` with `window: 2`. Joined
  `embedded_content` regularly exceeds the 1500-char ceiling. With no
  `if_oversize` set, you get **one WARN line** in stderr (deduped per
  chunker instance) and the oversize chunks are still written — your
  embedder will silently truncate them.
- `with-fallback.yaml`: same shape, but `if_oversize: fixed_overlap`
  re-chunks any overflow into 200-word windows that fit the ceiling.
  No WARN. No oversize rows.

## Run it

```bash
export CHUNKSHOP_DSN=postgresql://postgres:postgres@localhost:5432/mydb
docs/samples/if-oversize/run_demo.sh
```

You should see:

```
=== Python: no fallback ===
  WARN lines: 1 (expect ≥1)
  Rows with embedded_content > 1500: 7 (expect ≥1)
=== Python: with fallback ===
  WARN lines: 0 (expect 0)
  Rows with embedded_content > 1500: 0 (expect 0)
```

## When to set `if_oversize`

Set it whenever you use a wrapper chunker (`neighbor_expand`,
`summary_embed`, `hierarchical_summary`) or `fixed_overlap` with `max_chars`
and you don't want silent embedder truncation. The `fixed_overlap` fallback
shown here is the safe default — it's deterministic, fast, and char-bounded.

## How the ceiling resolves

For wrappers, the effective ceiling is the first non-None of:
1. `cfg.max_chars` set on the wrapper itself
2. `base.max_chars` from the wrapped chunker
3. `None` (no enforcement; `if_oversize` would be rejected at config-load)

For `fixed_overlap`, set `max_chars` explicitly — without it there's no
character ceiling at all (only `window_words`).

## Cross-language

The same YAML works from both `chunkshop` (Python) and `chunkshop-rs`
(Rust). Both produce 768-dim vectors in the same target table layout.

See [`docs/chunkers.md`](../../chunkers.md#what-happens-when-a-chunk-would-exceed-max_chars)
for the per-chunker oversize-behavior table.
