# `code_summary` extractor

**Module**: `chunkshop.extractors.code_summary`
**Type**: Extractor
**Ship status**: verified
**Optional extra**: `chunkshop[lede]` (only for `backend="lede"`; the
other backends are zero-dep or BYO)
**Since**: 2026-05-25 (commit `8b72252`, SP-D)

## Purpose

Generate a 1-3 sentence natural-language summary for every code chunk
and stamp it as `metadata.summary`. Optionally also stamps a
file-level rollup as `metadata.file_summary` on the first chunk of
each file.

Three pluggable backends:

- **`lede`** (default) — chunkshop's extractive lede summarizer
  (requires the `[lede]` extra).
- **`callable`** — bring-your-own summarizer at `"module.path:function"`.
- **`first_n_sentences`** — zero-dep regex sentence-boundary fallback.

If `backend="lede"` is requested but lede isn't installed, the
extractor transparently falls back to `first_n_sentences` and emits a
one-time `RuntimeWarning` per process.

## Config schema

`chunkshop.config.CodeSummaryExtractor` (pydantic v2, `extra="forbid"`):

| Field           | Type                                                | Default     | Notes |
|-----------------|-----------------------------------------------------|-------------|-------|
| `type`          | `Literal["code_summary"]`                            | **Required** | Discriminator. |
| `backend`       | `Literal["lede", "callable", "first_n_sentences"]`   | `"lede"`    | Summarizer backend. |
| `callable_path` | `str?`                                               | `None`      | `"module.path:function"` — only consulted when `backend="callable"`. |
| `max_length`    | `int`                                                | `300`       | `ge=1`. Character ceiling. The whole-sentence boundary means actual summary may be < `max_length` but never >. |
| `file_summary`  | `bool`                                               | `True`      | When False, never stamps `file_summary`. |

## Public API

```python
from chunkshop.extractors.code_summary import CodeSummaryExtractor
from chunkshop.extractors.result import ExtractResult

class CodeSummaryExtractor:
    def __init__(self, cfg: CodeSummaryExtractorCfg) -> None: ...

    def extract(
        self,
        text: str,
        chunk_metadata: Optional[dict] = None,
    ) -> ExtractResult: ...
```

`chunk_metadata` is an optional kwarg the chunker / runner can pass so
the file-summary heuristic works. From the default runner code path
the kwarg is absent → only `summary` is stamped.

## Behavior contract

1. **Always stamps `summary`** on non-empty chunks.
2. **Stamps `file_summary` only when `chunk_metadata` is passed** AND
   `cfg.file_summary=True` AND the metadata indicates "first chunk":
   - `chunk_metadata.start_line == 1`, OR
   - `chunk_metadata.symbol_type == "module"`.
3. **v1 simplification**: `file_summary` equals the first chunk's
   summary. A true cross-symbol rollup requires a `finalize()` pass —
   deferred to a future runner change.
4. **Empty input** returns `ExtractResult(tags=[], metadata={"summary": ""})`.
5. **Lazy imports.** Lede / vendor SDK imports don't happen at
   `load_extractor` time — only on first `extract()` call.
6. **Lede fallback warning** is `RuntimeWarning`, one per process,
   tracked via the class-level `_lede_fallback_warned` flag. After
   the fallback, `_effective_backend = "first_n_sentences"` for the
   rest of the extractor's lifetime.
7. **`callable` backend** validates the path on first call:
   - `path` must contain `:`
   - module must be importable
   - attribute must exist on the module
   - attribute must be callable
   All four failure modes raise `ValueError` with a clear message.
8. **`callable` invocation:** `fn(text, max_length=cfg.max_length)`.
   Your callable must accept `max_length` as a kwarg.

## Inputs

- Chunk text.
- Optional `chunk_metadata` dict (typically the chunk's own metadata).

## Outputs

- `ExtractResult(tags=[], metadata={"summary": "..."})` (always).
- If `file_summary` conditions met: `metadata["file_summary"]` added.

## Errors

| Exception | When |
|-----------|------|
| `ValueError` | `backend="callable"` and `callable_path` not set / malformed / import-fails / attribute missing / not callable. |
| `RuntimeWarning` | `backend="lede"` requested but lede missing (once per process). |
| Indirect | Whatever your `callable` raises. |

## Example: minimal (default lede backend)

```yaml
extractor:
  type: code_summary
```

## Example: zero-dep first_n_sentences

```yaml
extractor:
  type: code_summary
  backend: first_n_sentences
  max_length: 200
  file_summary: false
```

## Example: BYO summarizer (e.g. OpenAI)

```python
# my_app/summarizers.py
import openai
client = openai.OpenAI()

def summarize(text: str, max_length: int = 300) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"Summarize in <{max_length} characters."},
            {"role": "user", "content": text},
        ],
    )
    return resp.choices[0].message.content[:max_length]
```

```yaml
extractor:
  type: code_summary
  backend: callable
  callable_path: "my_app.summarizers:summarize"
  max_length: 300
```

## Example: composite with relationships

```yaml
extractor:
  type: composite
  extractors:
    - type: code_summary
      backend: lede
      max_length: 280
      file_summary: true
    - type: code_relationships
```

## How it integrates with the pipeline

`CodeSummaryExtractor` is loaded via
`chunkshop.extractors.load_extractor(cfg)` on the discriminator `type:
code_summary`. The runner feeds it chunk text post-chunking,
pre-embedding. To get `file_summary` populated, the chunker must
upstream-stamp `start_line: 1` or `symbol_type: "module"` on the
chunk's metadata (which `symbol_aware` does automatically).

Pair with `target.promote_metadata` to surface `summary` as a real
column so it can be filtered + returned in `chunkshop search` results:

```yaml
target:
  promote_metadata:
    - {path: summary,      type: text}
    - {path: file_summary, type: text}
```

## Tests proving the contract

- `tests/chunkshop/test_extractor_code_summary.py`:
  - lede backend round-trip (when extra present)
  - lede-missing → falls back to first_n_sentences with RuntimeWarning
  - first_n_sentences regex sentence boundary
  - empty input returns empty summary
  - file_summary stamps only when chunk_metadata signals "first chunk"
  - callable backend path validation (4 failure modes)
  - callable backend round-trip with a fake summarizer

## See also

- Reference: [`chunker-symbol-aware`](chunker-symbol-aware.md)
- Reference: [`extractor-code-relationships`](extractor-code-relationships.md)
- [`docs/cookbook/code-search.md`](../cookbook/code-search.md)
- [`docs/summaries.md`](../summaries.md) — chunkshop's broader summary surface
