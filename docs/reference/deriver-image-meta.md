# `image_meta` deriver (rung 0 — deterministic image metadata)

**Module**: `chunkshop.sources.derivers.image_meta`
**Type**: Deriver — rung 0 of the media escalation ladder
**Ship status**: verified
**Optional extra**: none (Pillow arrives with `fastembed`, already a core dependency)
**Since**: 2026-09-01 (commit `18aef64`; Rust `149432e`)

## Purpose

Turn an image into searchable text and exact metadata with **no model, no
network, and no API key**. The text is the humanised filename, optionally
followed by the humanised trailing path segments; the metadata is the image's
header facts.

It is the floor of the ladder: deterministic, hallucination-proof, and cheap
enough to leave on. It is also **header-only** — it never decodes the raster,
because every fact it needs (`width`, `height`, format, mode, alpha) lives in
the file header. A 640 KB palette PNG that expands to 139 megapixels costs the
same as a 128×128 sprite, and formats whose pixels the decoder cannot produce
(palette BMP, palette TIFF, 8-bit greyscale+alpha TIFF) still read correctly.

For tags derived from the *pixels* rather than the path, use the
[`clip_tags`](deriver-clip-tags.md) rung, which wraps this one and appends to
its output.

## Config schema

`chunkshop.config.ImageMetaDeriverConfig` (pydantic v2, `extra="forbid"`;
Rust `#[serde(deny_unknown_fields)]`). Configured under `source.derive`, keyed
by a pipe-separated extension list.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `rung` | `Literal["image_meta"]` | **Required** | Discriminator. Note the key is `rung`, not `type`. |
| `path_segments` | `bool` | `True` | Append the humanised trailing parent directory names to the derived text. |
| `segment_depth` | `int` | `3` | How many trailing parent directories `path_segments` consumes. |

```yaml
source:
  type: files
  glob: /assets/**/*
  derive:
    png|jpg|jpeg|webp|gif|bmp|tiff|tif:
      rung: image_meta
      path_segments: true
      segment_depth: 3
```

## Public API

```python
from chunkshop.sources.derivers.image_meta import ImageMetaDeriver, humanise

class ImageMetaDeriver:
    supported_extensions = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif"]

    def __init__(self, path_segments: bool = True, segment_depth: int = 3) -> None: ...
    def derive(self, path: Path) -> ParseResult: ...
    def derivation_hash(self) -> str: ...
    def cache_key_extra(self, path: Path) -> str | None: ...
    def path_text(self, path: Path) -> str: ...

def humanise(stem: str) -> str: ...   # "goblin_warrior-02" -> "goblin warrior 02"
```

Construct via `chunkshop.sources.derivers.load_deriver(cfg)`; `FilesSource`
does this for you from the `derive:` block.

## Behavior contract

1. **Text is `humanise(stem)`, then the humanised path segments.** `humanise`
   splits on `_`, `-` and `.` and rejoins with spaces. With
   `path_segments: true`, the last `segment_depth` parent directory names are
   humanised and appended, in path order.
2. **`path_text()` is the single source of that material.** `derive()` builds
   its output from it and `cache_key_extra()` returns it verbatim, so the two
   can never drift into disagreeing about what path material was consumed.
3. **Empty text has a format fallback.** A stem that humanises to nothing
   (e.g. `_.png`) with no usable segments falls back to `"<format> image"`,
   so this rung never trips `on_empty`. The fallback depends on the image
   format rather than the path, so it is deliberately not key material.
4. **Header-only.** `Image.open` is lazy and `.size` / `.mode` / `.format`
   read from the container header; no pixels are decoded.
5. **`cache_key_extra` returns the path-derived text, not the path.** With
   `path_segments: false` the parent directory is correctly absent from the
   cache key, and a rename that leaves the humanised text unchanged
   (`a-b.png` → `a_b.png`) still hits, because the derived text is genuinely
   identical. See the cache-soundness invariant in
   [`../media-ingest.md`](../media-ingest.md#the-soundness-invariant-and-why-cache_key_extra-exists).
6. **Provenance is always stamped** — `rung="image_meta"`,
   `model="deterministic"`, `model_version="1"`, `engine="python"` (Rust:
   `"rust"`).

## Metadata stamped

```python
{
    "width": 320,                 # int, from the header
    "height": 220,                # int, from the header
    "image_format": "PNG",        # Pillow's format id, or the uppercased suffix
    "image_mode": "RGB",          # PIL mode: RGB, RGBA, P, L, LA, 1, CMYK, I;16, ...
    "aspect_ratio": 1.4545,       # round(width / height, 4); 0.0 when height == 0
    "has_alpha": False,           # RGBA/LA/PA, or a `transparency` key in img.info
    # plus the provenance block stamped by every deriver:
    "content_origin": "derived",
    "derived_rung": "image_meta",
    "derived": {"rung": ..., "model": ..., "model_version": ...,
                "engine": ..., "params_hash": ..., "ts": ...},
}
```

`aspect_ratio` rounds **half to even** (Python's `round`); the Rust port was
matched to that after an 800×1024 screenshot came out `0.7812` in one port and
`0.7813` in the other.

## Inputs

A path to one of `supported_extensions`. `FilesSource` routes purely on the
extensions named in the `derive:` key, so the list above is the tool's
coverage rather than a gate.

## Outputs

| Field | Value |
|-------|-------|
| `ParseResult.text` | Humanised stem, then humanised path segments; format fallback if empty. |
| `ParseResult.metadata` | The six fields above plus the provenance block. |

## Errors

| Exception | When |
|-----------|------|
| `ParserError` | Pillow cannot open the file (corrupt, truncated, not an image). Message names the path and the underlying error. |
| `RuntimeError` | Pillow is not importable. Should not occur — it ships with `fastembed`. |
| `TypeError` | A config value is not JSON-serializable, so `params_hash` cannot be computed. Names the offending key. |

## Example: minimal

```yaml
source:
  type: files
  glob: /assets/**/*.png
  derive:
    png:
      rung: image_meta
```

## Example: realistic (path-as-taxonomy, with promoted columns)

A corpus laid out as `assets/<type>/<style>/<subject>.png`, where the
directory names carry the meaning:

```yaml
cell_name: asset_index
source:
  type: files
  glob: /assets/**/*.png
  id_from: path
  derive:
    png|jpg|jpeg|webp:
      rung: image_meta
      segment_depth: 2        # <type>/<style>, not the whole tree
chunker:  {type: fixed_overlap, window_words: 512, step_words: 512}
embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: assets
  table: asset_index
  mode: append
  source_tag: sprites
  promote_metadata:
    - path: width
      type: int
    - path: height
      type: int
    - path: image_format
      type: text
    - path: has_alpha
      type: boolean
```

`content_origin` and `derived_rung` are added to `promote_metadata`
automatically because the cell configures `source.derive`.

## How it integrates with the pipeline

`FilesSource` layers the `derive:` block over its per-extension parser table,
so a deriver occupies the same slot a `FileParser` does. Its `ParseResult`
becomes the `Document`'s `content` and `metadata`, and everything downstream —
chunker, embedder, extractor, sink, `chunkshop search` — is unchanged. Derived
metadata is merged with **source keys winning**: `source_path` and `parser`
are stamped by `FilesSource` over anything a deriver emitted under those names.

Pair it with `path_segments: false` when directory names are UUIDs or build
hashes rather than taxonomy — whether the segments help is corpus-dependent
and unmeasured.

## Tests proving the contract

- `python/tests/chunkshop/test_deriver_image_meta.py`:
  - dimensions/format emitted; text includes the humanised filename
  - path segments included, and bounded by `segment_depth`
  - provenance stamped; `derivation_hash` matches what `stamp_provenance` records
  - never returns empty text (separator-only stem, with and without segments)
  - a tripwire test that fails if the two params literals drift apart
- `python/tests/chunkshop/test_files_derive_cache.py` — byte-identical images
  under different names get their own text; renaming re-derives.
- `rust/chunkshop/tests/media_derivation_parity.rs` — all six fields asserted
  **value-level** across ports, including `aspect_ratio`.
- Sample: [`docs/samples/images-keyless.yaml`](../samples/images-keyless.yaml)
  uses rung 1, which wraps this rung.

## See also

- Reference: [`deriver-clip-tags`](deriver-clip-tags.md) — rung 1, wraps this one
- Reference: [`deriver-ross`](deriver-ross.md) — rung 5, the subprocess escape hatch
- Reference: [`parsers`](parsers.md) — the dispatch layer `derive:` layers over
- [`docs/media-ingest.md`](../media-ingest.md) — the ladder, the cache, provenance
