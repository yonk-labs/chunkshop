# `image_meta` parity fixtures

Pillow-generated images covering the colour models the rung-0 `image_meta`
deriver has to report, plus `expected.json` — **what Pillow itself says about
each of them**.

Pillow is the reference implementation; the Rust port is checked against these
recorded values by
`rust/chunkshop/src/sources/derivers/image_meta.rs`'s unit tests and
`rust/chunkshop/tests/media_derivation_parity.rs`.

## Why the expectations are committed rather than computed

The tests these replaced built a palette PNG with the `png` crate, read it
back with the `png` crate, and asserted a hand-written expectation. That
proves the Rust reader is self-consistent and says nothing about whether it
agrees with Pillow — the golden-fixture-regenerated-by-the-implementation-it-
checks anti-pattern. Here the inputs come from Pillow and the expectations
come from Pillow, so the only way to pass is to actually match it.

`expected.json` is computed by the exact expressions
`python/src/chunkshop/sources/derivers/image_meta.py` uses:

```python
width, height = img.size
fmt        = img.format or path.suffix.lstrip(".").upper()
mode       = img.mode
has_alpha  = mode in ("RGBA", "LA", "PA") or "transparency" in img.info
aspect     = round(width / height, 4)
```

## What each fixture is for

| file | Pillow mode | why it is here |
|---|---|---|
| `png_rgb.png` / `png_rgba.png` | `RGB` / `RGBA` | baseline |
| `png_l.png` / `png_la.png` | `L` / `LA` | greyscale, greyscale+alpha |
| `png_1.png` | `1` | 1-bit; a decoder that expands to 8-bit reports `L` |
| `png_i16.png` | `I;16` | 16-bit greyscale; `image` decodes `Luma16` and reports `L` |
| `png_p.png` / `png_p_trns.png` | `P` | indexed, and indexed with a `tRNS` chunk (`has_alpha` with no alpha channel) |
| `png_800x1024.png` | `RGB` | an aspect ratio that actually rounds: 0.78125 is a tie at 4 dp, and Python breaks ties to even (0.7812), not away from zero (0.7813) |
| `png_big_palette.png` | `P` | 14432x9664 = 139 Mpx, **136 KB on disk**, 558 MB decoded as RGBA. Over `image`'s 512 MiB `max_alloc`, so a naive full decode fails with `Memory limit exceeded` where Pillow succeeds — the rung-0/rung-1 parity bug both ports are now pinned against (see below). Palette PNGs compress ~200:1, so file size does not predict decode cost — which is why no ordinary synthetic fixture caught this |
| `jpg_rgb.jpg` / `jpeg_rgb.jpeg` | `RGB` | both spellings of the extension |
| `jpg_l.jpg` | `L` | single-component JPEG |
| `webp_rgb.webp` / `webp_rgba.webp` | `RGB` / `RGBA` | WebP with and without alpha |
| `gif_p.gif` / `gif_p_trns.gif` | `P` | `image` reports alpha for every GIF; only the Graphic Control Extension says whether it is real |
| `bmp_1.bmp` | `1` | 2-colour palette that is the greyscale ramp — Pillow drops the palette |
| `bmp_l.bmp` | `L` | 256-entry greyscale palette, likewise |
| `bmp_p.bmp` | `P` | a real colour palette; `image` cannot decode this one at all |
| `bmp_rgb.bmp` / `bmp_rgba.bmp` | `RGB` / `RGB` | note both: a 32-bit BI_RGB BMP is `RGB` in Pillow, because the fourth byte is reserved rather than alpha |
| `tif_1.tiff` | `1` | bilevel |
| `tif_l.tiff` / `tif_la.tiff` | `L` / `LA` | greyscale; the `LA` case is one `image` refuses (`color type Unknown(16)`) |
| `tif_p.tiff` | `P` | palette TIFF; `image` refuses it (`Photometric interpretation RGBPalette ... unsupported`) |
| `tif_rgb.tiff` / `tif_rgba.tiff` | `RGB` / `RGBA` | baseline |
| `tif_cmyk.tiff` | `CMYK` | a colour model `image`'s `ColorType` cannot express |
| `tif_i16.tiff` | `I;16` | 16-bit greyscale |
| `tif_rgb.tif` | `RGB` | the other spelling of the extension |

Every extension in `ImageMetaDeriver.supported_extensions` has at least one
fixture, and both `generate.py` and a Rust test assert that.

## The decompression-bomb bands

`png_big_palette.png` is here because it is the file that separates the two
ports. Rung 0 (`image_meta`) never decodes it — it reads the PNG header, which
is why `image_meta.rs` is untouched by any of this. Rung 1 (`clip_tags`) has to
decode it, because the CLIP vision tower needs pixels, and that decode has to
be governed by the same policy Pillow applies.

Pillow's policy is `PIL.Image._decompression_bomb_check`, three bands around
`PIL.Image.MAX_IMAGE_PIXELS`. Both comparisons are strict `>`, so a count
landing exactly on a constant takes the gentler branch. Measured against the
installed Pillow, and asserted case-for-case by
`clip_preprocess.rs::the_bands_match_pils_decompression_bomb_check`:

| dimensions | pixels | Pillow 12.3.0 | `decompression_bomb_check` |
|---|---|---|---|
| 1x1 | 1 | Allow | Allow |
| 9458x9458 | 89,453,764 | Allow | Allow |
| 5x17895697 | 89,478,485 (= `MAX`) | Allow | Allow |
| 10x8947849 | 89,478,490 | Warn | Warn |
| 14432x9664 | 139,470,848 (`png_big_palette.png`) | Warn | Warn |
| 10x17895697 | 178,956,970 (= `2 * MAX`) | Warn | Warn |
| 20x8947849 | 178,956,980 | Reject | Reject |
| 65536x65536 | 4,294,967,296 | Reject | Reject |
| 100000x100000 | 10,000,000,000 | Reject | Reject |
| 0x0 | 1 (`max(1, ..)` per axis) | Allow | Allow |
| 0x100000000 | 100,000,000 | Warn | Warn |

Regenerate the Pillow column with:

```python
import warnings
from PIL import Image

def verdict(w, h):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            Image._decompression_bomb_check((w, h))
        except Image.DecompressionBombError:
            return "Reject"
    return "Warn" if any(issubclass(c.category, Image.DecompressionBombWarning)
                         for c in caught) else "Allow"
```

## Regenerating

    cd python && uv run --no-sync python ../tests/fixtures/images/generate.py

Deterministic — no randomness. Regenerate only when deliberately adding a
case; regenerating to make a failing test pass would defeat the point.

## Why they live here

Not in `docs/samples/images/`: that directory is the SC-1 quickstart corpus,
globbed as `*.png` by `docs/samples/images-keyless.yaml` and asserted on by
`.github/workflows/sc1-quickstart-clock.yml`. Adding images there dilutes the
quickstart's ranking margin down to bare RRF rank spacing — which is exactly
what happened while these files sat in that directory.
