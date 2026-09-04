# `clip_tags` deriver (rung 1 — keyless zero-shot CLIP tagging)

**Module**: `chunkshop.sources.derivers.clip_tags`
**Type**: Deriver — rung 1 of the media escalation ladder
**Ship status**: verified
**Optional extra**: none — `fastembed` is already a core dependency (`chunkshop[vision]` exists but is deliberately empty)
**Since**: 2026-09-01 (commit `2f8a0ee`; Rust `f8c9e09`)

## Purpose

Make images searchable from their **pixels**, with no API key, no GPU, and no
new dependency. The deriver embeds a label vocabulary you supply with the CLIP
**text** tower, embeds each image with the CLIP **vision** tower, and appends
the nearest labels to rung 0's text.

The output is **text**, so it lands in the ordinary text plane alongside email
and PDF and is served by the hybrid search that already ships. There is no
second vector space and no fusion step.

This rung wraps [`image_meta`](deriver-image-meta.md) rather than replacing it:
its text is `image_meta`'s text plus the winning tags, and its metadata is
`image_meta`'s metadata plus `clip_tags` / `clip_scores`.

> **The vocabulary is the whole game.** A generic vocabulary produces generic
> tags. `ClipTagsDeriver` rejects an empty one on purpose. chunkshop ships no
> default vocabulary and asserts no winner — choose the labels for your
> corpus, and bake them off if it matters.

## Config schema

`chunkshop.config.ClipTagsDeriverConfig` (pydantic v2, `extra="forbid"`;
Rust `#[serde(deny_unknown_fields)]`). Configured under `source.derive`, keyed
by a pipe-separated extension list.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `rung` | `Literal["clip_tags"]` | **Required** | Discriminator. Note the key is `rung`, not `type`. |
| `vocabulary` | `list[str]` | **Required** | Candidate labels. Must be non-empty. Free-form natural-language phrases work better than bare nouns — CLIP was trained on captions. |
| `top_k` | `int` | `3` | Keep at most this many highest-scoring labels. |
| `min_score` | `float` | `0.20` | Drop labels scoring below this cosine similarity. A label can be in the top *k* and still be dropped. |
| `vision_model` | `str` | `"Qdrant/clip-ViT-B-32-vision"` | Image tower. |
| `text_model` | `str` | `"Qdrant/clip-ViT-B-32-text"` | Label tower. Must share a vector space with `vision_model`. |

The wrapped `image_meta` deriver is constructed with **its own defaults**
(`path_segments=True`, `segment_depth=3`) and is not configurable from this
block.

## Public API

```python
from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

class ClipTagsDeriver:
    supported_extensions = ImageMetaDeriver.supported_extensions

    def __init__(self, vocabulary: Sequence[str], top_k: int = 3,
                 min_score: float = 0.20,
                 vision_model: str = "Qdrant/clip-ViT-B-32-vision",
                 text_model: str = "Qdrant/clip-ViT-B-32-text") -> None: ...
    def derive(self, path: Path) -> ParseResult: ...
    def derivation_hash(self) -> str: ...
    def cache_key_extra(self, path: Path) -> str | None: ...
```

Construct via `chunkshop.sources.derivers.load_deriver(cfg)`.

## Behavior contract

1. **Rung 0 first, then tags.** `derive()` calls the wrapped
   `ImageMetaDeriver`, then appends the selected labels:
   `" ".join([base.text, *tags]).strip()`.
2. **Label selection.** Both towers' vectors are L2-normalised, scores are the
   dot product, the top `top_k` by score are taken, and any scoring below
   `min_score` are then dropped. An image can therefore legitimately produce
   **zero** tags — in which case the text is rung 0's text alone, which is
   never empty.
3. **Models load lazily and once.** Nothing downloads at install or at import;
   the ~0.6 GB of CLIP weights download on the first *use* of a cell that
   configures this rung. The label matrix is embedded once per deriver
   instance, not once per image.
4. **An empty vocabulary is a `ValueError` at construction**, before any model
   loads and before the sink's DDL runs.
5. **`model_version` is the installed `fastembed` version**, not a constant. A
   fastembed upgrade can change the ONNX weights behind an unchanged model id,
   which would otherwise serve stale cached tags indefinitely. Lookup failure
   degrades to `"unknown"` — i.e. "always re-derive" — and never raises.
6. **`cache_key_extra` delegates to the wrapped `image_meta`.** Without this,
   two byte-identical images with different names would share one entry and
   the second would be served the first's filename text *and* the first
   image's tags.
7. **Provenance names both towers.** `model` is `"<vision_model>+<text_model>"`.

## Metadata stamped

```python
{
    # everything image_meta stamps:
    "width": 320, "height": 220, "image_format": "PNG", "image_mode": "RGB",
    "aspect_ratio": 1.4545, "has_alpha": False,
    # plus:
    "clip_tags":   ["a chart or diagram"],   # list[str], selected labels, best first
    "clip_scores": [0.2161],                 # list[float], round(score, 4), same order
    # plus the provenance block:
    "content_origin": "derived",
    "derived_rung": "clip_tags",
    "derived": {
        "rung": "clip_tags",
        "model": "Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text",
        "model_version": "0.8.0",            # the installed fastembed version
        "engine": "python",                  # "rust" on the Rust port
        "params_hash": "40667db93985609a",
        "ts": "2026-09-01T22:38:58.573509+00:00",
    },
}
```

`clip_scores` rounds **half to even**; the Rust port was matched to that.

## Inputs

A path to one of `supported_extensions` (the same eight image extensions
`image_meta` covers). Non-image bytes reach Pillow and fail as a
`ParserError` from rung 0.

## Outputs

| Field | Value |
|-------|-------|
| `ParseResult.text` | Rung 0's text, then the selected labels, space-joined. |
| `ParseResult.metadata` | Rung 0's six fields, `clip_tags`, `clip_scores`, and the provenance block. |

## Errors

| Exception | When |
|-----------|------|
| `ValueError` | `vocabulary` is empty, at construction. |
| `ParserError` | Pillow cannot open the file (raised by the wrapped rung 0). |
| `ImportError` / fastembed's own errors | The CLIP weights cannot be downloaded or loaded on first use. |
| `TypeError` | A config value is not JSON-serializable, so `params_hash` cannot be computed. Names the offending key. |

On the Rust port, a YAML asking for `rung: clip_tags` in a binary built
without the `media` **and** `embedder-hub` Cargo features fails at **config
load** with a message naming the features, not with a missing symbol.

## Example: minimal

```yaml
source:
  type: files
  glob: /assets/**/*.png
  derive:
    png:
      rung: clip_tags
      vocabulary:
        - a game sprite or pixel art
        - a chart or diagram
        - a screenshot of a user interface
```

## Example: realistic (the shipped keyless recipe)

[`docs/samples/images-keyless.yaml`](../samples/images-keyless.yaml), runnable
as printed from a repo clone:

```yaml
cell_name: images_keyless
source:
  type: files
  glob: docs/samples/images/*.png
  id_from: stem
  derive:
    png|jpg|jpeg|webp:
      rung: clip_tags
      vocabulary:
        - a screenshot of a user interface
        - a photograph of a person
        - a chart or diagram
        - a game sprite or pixel art
        - a scanned document page
      top_k: 2
chunker:
  type: fixed_overlap
  window_words: 512
  step_words: 512
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
extractor:
  type: none
target:
  type: sqlite
  dsn_env: SQLITE_PATH
  database: ignored
  table: images
  mode: overwrite
  hnsw: false
```

```bash
export SQLITE_PATH=/tmp/chunkshop-images.db
chunkshop ingest --config docs/samples/images-keyless.yaml
chunkshop search --config docs/samples/images-keyless.yaml \
  --query "pixel art game character" --k 3
```

```
1. [0.0164] sprite_knight#0  sprite knight docs samples images a game sprite or pixel art  path=docs/samples/images/sprite_knight.png
2. [0.0161] screenshot_settings#0  screenshot settings docs samples images a screenshot of a user interface a chart or diagram  path=docs/samples/images/screenshot_settings.png
3. [0.0159] chart_revenue#0  chart revenue docs samples images a chart or diagram  path=docs/samples/images/chart_revenue.png
```

Measured on one developer machine over that three-image corpus with weights
already cached: **2.3 s cold cache, 0.5 s warm.**

## How it integrates with the pipeline

Identical to any other deriver: `FilesSource` layers it over the parser table,
its `ParseResult` becomes the `Document`, and chunker/embedder/extractor/sink
are unchanged. Because the output is text, `chunkshop search` needs no flag
and no second table.

Note that the embedder in the cell (`bge-base-en-v1.5-int8` above) is **not**
CLIP — CLIP produced *tags*, and those tags are then embedded like any other
text by whatever embedder the cell configures. The two model choices are
independent.

Rung 0's `path_segments` is on inside this rung, so directory names reach the
embedded text (`docs samples images` in the output above). Whether that helps
or dilutes is corpus-dependent and unmeasured.

## Tests proving the contract

- `python/tests/chunkshop/test_deriver_clip_tags.py`:
  - a non-empty vocabulary is required
  - tags are text and correct; rung-0 metadata is included
  - provenance names both towers
  - `derivation_hash` changes when the fastembed version changes, and matches
    what `stamp_provenance` records
  - `model_version` falls back to `"unknown"` on lookup failure
  - `cache_key_extra` is delegated to the wrapped `image_meta` deriver
- `python/tests/chunkshop/test_quickstart_sc1.py` — the shipped sample is a
  valid config, uses no extras-requiring rung, and the README block matches
  the sample file byte-for-byte.
- `rust/chunkshop/tests/clip_embedding_parity.rs` — SC-12 bit-level vector
  parity across ports over a 28-image corpus.
- `rust/chunkshop/tests/media_derivation_parity.rs` — contract-level schema
  parity for the derived rows.

## See also

- Reference: [`deriver-image-meta`](deriver-image-meta.md) — rung 0, wrapped by this one
- Reference: [`deriver-ross`](deriver-ross.md) — rung 5, natural-language descriptions and audio
- Reference: [`embedder-openai`](embedder-openai.md) — the other "point it at an endpoint" surface
- [`docs/media-ingest.md`](../media-ingest.md) — the ladder, the cache, Rust parity
- [`docs/samples/images-keyless.yaml`](../samples/images-keyless.yaml) — runnable recipe
