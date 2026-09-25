"""Pillow-generated `image_meta` fixtures + the PIL reference values for them.

Two outputs, both committed:

1. The image files in this directory — one per (format, colour-model) pair
   the `image_meta` rung-0 deriver can be pointed at. Every extension in
   ``ImageMetaDeriver.supported_extensions`` has at least one fixture.
2. ``expected.json`` — what **Pillow** reports for each of them
   (``width``/``height``/``image_format``/``image_mode``/``aspect_ratio``/
   ``has_alpha``), computed by the exact expressions
   ``sources/derivers/image_meta.py`` uses.

The Rust value-parity test (``rust/chunkshop/tests/media_derivation_parity.rs``)
reads ``expected.json`` and asserts the Rust deriver reproduces it. That is the
point of the split: the expectations come from Pillow, never from the Rust
reader being checked. A test that builds a file with the ``png``/``gif`` crates
and reads it back with the same crates proves only self-consistency — it is the
golden-fixture-regenerated-by-the-implementation-it-checks anti-pattern, and it
is what these fixtures replaced.

Regenerate with (from the repo root):

    cd python && uv run --no-sync python ../tests/fixtures/images/generate.py

Deterministic: no randomness, fixed sizes and colours. Byte-level PNG output
can shift with the underlying Pillow/zlib version, but never the pixels and
never the header fields ``expected.json`` records.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent


# --- builders -------------------------------------------------------------
def rgb(size=(8, 8)) -> Image.Image:
    return Image.new("RGB", size, (30, 110, 200))


def rgba() -> Image.Image:
    img = Image.new("RGBA", (8, 8), (200, 30, 30, 255))
    px = img.load()
    for y in range(4):
        for x in range(8):
            px[x, y] = (200, 30, 30, 0)
    return img


def grayscale_l() -> Image.Image:
    img = Image.new("L", (8, 8), 128)
    px = img.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = (x * 32) % 256
    return img


def grayscale_la() -> Image.Image:
    img = Image.new("LA", (8, 8), (128, 255))
    px = img.load()
    for y in range(4):
        for x in range(8):
            px[x, y] = (128, 0)
    return img


def bilevel_1() -> Image.Image:
    img = Image.new("1", (8, 8), 0)
    px = img.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = 1 if (x + y) % 2 == 0 else 0
    return img


def depth16() -> Image.Image:
    """16-bit greyscale. PIL mode "I;16"; the `image` crate decodes Luma16."""
    img = Image.new("I;16", (8, 8), 0)
    px = img.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = 30000 if (x + y) % 2 == 0 else 500
    return img


def palette_p() -> Image.Image:
    img = Image.new("P", (8, 8))
    palette = [0, 0, 0] * 256
    palette[0:3] = [255, 0, 0]
    palette[3:6] = [0, 255, 0]
    img.putpalette(palette)
    px = img.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = 0 if (x + y) % 2 == 0 else 1
    return img


def palette_trns() -> Image.Image:
    img = palette_p()
    img.info["transparency"] = 0
    return img


def cmyk() -> Image.Image:
    return Image.new("CMYK", (8, 8), (10, 20, 30, 40))


def big_palette_png(path: Path) -> None:
    """A physically large PALETTE PNG that is tiny on disk.

    14432x9664 = 139,470,848 px. Decoded to RGBA that is 558 MB, past the
    512 MiB ``image::Limits::default().max_alloc``, so a full-decode metadata
    path refuses it outright — while Pillow, which reads only the header,
    returns its size without touching a pixel. Palette PNGs compress ~200:1,
    so FILE SIZE DOES NOT PREDICT DECODE COST: this fixture is a few tens of
    KB on disk. That is exactly why no ordinary synthetic fixture caught the
    bug it pins.
    """
    img = Image.new("P", (14432, 9664), 0)
    palette = [0, 0, 0] * 256
    palette[0:3] = [255, 0, 0]
    palette[3:6] = [0, 255, 0]
    img.putpalette(palette)
    # The tRNS chunk is load-bearing, not decoration: without it the `image`
    # crate expands the palette to Rgb8 (3 B/px = 418 MB, just UNDER the
    # limit) and the bug does not reproduce. With it the expansion is Rgba8
    # (4 B/px = 558 MB) and the decode is refused.
    img.save(path, optimize=True, transparency=0)


# --- fixture table --------------------------------------------------------
# (filename, builder, save kwargs). Ordered by extension so the coverage
# assertion below reads as a checklist.
FIXTURES = [
    # PNG — every IHDR colour type plus the two bit depths that change the mode.
    ("png_rgb.png", rgb, {}),
    ("png_rgba.png", rgba, {}),
    ("png_l.png", grayscale_l, {}),
    ("png_la.png", grayscale_la, {}),
    ("png_1.png", bilevel_1, {}),
    ("png_i16.png", depth16, {}),
    ("png_p.png", palette_p, {}),
    ("png_p_trns.png", palette_trns, {}),
    # A non-square, non-trivially-rounding aspect ratio: 800/1024 = 0.78125,
    # a value exactly on a rounding boundary at 4 decimal places. Python's
    # round() is round-half-to-EVEN and gives 0.7812; naive
    # round-half-away-from-zero gives 0.7813.
    ("png_800x1024.png", lambda: rgb((800, 1024)), {}),
    # JPEG — both spellings of the extension, plus greyscale.
    ("jpg_rgb.jpg", rgb, {"quality": 90}),
    ("jpeg_rgb.jpeg", rgb, {"quality": 90}),
    ("jpg_l.jpg", grayscale_l, {"quality": 90}),
    # WebP.
    ("webp_rgb.webp", rgb, {}),
    ("webp_rgba.webp", rgba, {}),
    # GIF — with and without a transparent index.
    ("gif_p.gif", palette_p, {}),
    ("gif_p_trns.gif", palette_p, {"transparency": 0}),
    # BMP.
    ("bmp_1.bmp", bilevel_1, {}),
    ("bmp_l.bmp", grayscale_l, {}),
    ("bmp_p.bmp", palette_p, {}),
    ("bmp_rgb.bmp", rgb, {}),
    ("bmp_rgba.bmp", rgba, {}),
    # TIFF — both spellings of the extension.
    ("tif_1.tiff", bilevel_1, {}),
    ("tif_l.tiff", grayscale_l, {}),
    ("tif_la.tiff", grayscale_la, {}),
    ("tif_p.tiff", palette_p, {}),
    ("tif_rgb.tiff", rgb, {}),
    ("tif_rgba.tiff", rgba, {}),
    ("tif_cmyk.tiff", cmyk, {}),
    ("tif_i16.tiff", depth16, {}),
    ("tif_rgb.tif", rgb, {}),
]

BIG = "png_big_palette.png"


def pil_values(path: Path) -> dict:
    """EXACTLY what `ImageMetaDeriver.derive` reads out of Pillow."""
    with Image.open(path) as img:
        width, height = img.size
        fmt = img.format or path.suffix.lstrip(".").upper()
        mode = img.mode
        has_alpha = mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    return {
        "width": width,
        "height": height,
        "image_format": fmt,
        "image_mode": mode,
        "aspect_ratio": round(width / height, 4) if height else 0.0,
        "has_alpha": bool(has_alpha),
    }


def main() -> None:
    from chunkshop.sources.derivers.image_meta import ImageMetaDeriver

    OUT.mkdir(parents=True, exist_ok=True)
    for name, build, kwargs in FIXTURES:
        build().save(OUT / name, **kwargs)
    big_palette_png(OUT / BIG)

    names = [n for n, _, _ in FIXTURES] + [BIG]

    # Coverage gate: every extension the deriver claims to support must have
    # a fixture. A new supported extension without one fails here rather than
    # quietly shipping untested.
    covered = {Path(n).suffix.lstrip(".").lower() for n in names}
    missing = set(ImageMetaDeriver.supported_extensions) - covered
    if missing:
        raise SystemExit(f"no fixture for supported extension(s): {sorted(missing)}")

    expected = {n: pil_values(OUT / n) for n in sorted(names)}
    (OUT / "expected.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")

    for n in sorted(names):
        print(f"{n:24s} {(OUT / n).stat().st_size:>9,d} B  {expected[n]}")


if __name__ == "__main__":
    main()
