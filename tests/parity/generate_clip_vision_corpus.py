"""Build the SC-12 CLIP-vision parity corpus and its Python reference vectors.

Two outputs, both committed:

  * `rust/chunkshop/tests/parity-fixtures/clip-vision-corpus/*.png` — the
    images themselves.
  * `rust/chunkshop/tests/parity-fixtures/clip_vision_corpus_reference.bin` —
    Python `fastembed` vectors for them, in the `u32 n, u32 dim, f32[n][dim]`
    little-endian layout `clip_embedding_parity.rs::read_reference` already
    reads. Row order is `manifest.json`'s order.

  * `.../clip_vision_corpus_manifest.json` — the row order, plus each image's
    shape and content class, so the Rust test can report a blown bar with the
    kind of image that blew it instead of an index.

Run from the repo root:

    cd python && uv run --no-sync python ../tests/parity/generate_clip_vision_corpus.py

WHY THIS CORPUS EXISTS. The vision bar in `clip_embedding_parity.rs` has been
set twice from three fixtures, and been wrong twice. Those three
(`sprite_knight`, `chart_revenue`, `screenshot_settings`) are flat-shaded
low-frequency Pillow drawings and sit at the EASY end of the residual
distribution; an independent audit generated 13 ordinary photographs and 6 of
13 failed the bar calibrated on them. The residual is content-dependent, not
shape-dependent — a high-spatial-frequency text page passes easily, a 1280x720
photograph does not — so a bar can only be honest if it is set from a corpus
that spans the content space. This one does: real photographs across a spread of
resolutions and aspect ratios from 1:4 to 4:1, greyscale, high-frequency
texture and text, flat-shaded synthetic, and the 224x224 no-resize control
where the resampler is bypassed entirely and the two runtimes agree exactly.

Source images are scikit-image's bundled set — real photographs, not synthetic
drawings, and they ship with the package so this is a no-download run. They are
BSD-3-licensed with the rest of scikit-image.

DEPENDENCY: `scikit-image` is needed only by this generator, not by chunkshop
or its test suite. `uv pip install scikit-image` before running it; the
committed PNGs are what the test suite actually consumes.

Several rows are resampled here purely to cap the committed corpus's size —
this is a test fixture set, not an archive, and photographic PNGs do not
compress. The measured distribution below is measured on exactly these files,
so nothing is characterised at one resolution and committed at another.

Resampling done here (to build a shape) is Pillow LANCZOS and is irrelevant to
what is being measured: the output is a PNG on disk, and both runtimes then
decode the same bytes. What is measured is what each runtime does with those
bytes on the way to CLIP's 224x224 input.
"""
from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

# Single thread: ORT's reduction order is thread-count dependent, and the Rust
# test pins the same vars. Matches `generate_clip_full_vectors.py`.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"
CORPUS_DIR = FIXTURE_DIR / "clip-vision-corpus"
MANIFEST = FIXTURE_DIR / "clip_vision_corpus_manifest.json"
REFERENCE = FIXTURE_DIR / "clip_vision_corpus_reference.bin"
SAMPLE_IMAGES = REPO_ROOT / "docs" / "samples" / "images"


def _build_corpus() -> list[dict]:
    """Write every corpus image and return the manifest rows, in row order."""
    from PIL import Image
    from skimage import data

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    def src(name: str) -> "Image.Image":
        return Image.fromarray(getattr(data, name)())

    rows: list[dict] = []

    def emit(name: str, img: "Image.Image", content: str, note: str) -> None:
        path = CORPUS_DIR / f"{name}.png"
        img.save(path, optimize=True)
        # Read back rather than trusting `img`: the manifest must describe the
        # bytes on disk, which is what both runtimes will decode.
        with Image.open(path) as reread:
            w, h = reread.size
            mode = reread.mode
        rows.append(
            {
                "name": f"{name}.png",
                "width": w,
                "height": h,
                "mode": mode,
                "aspect": round(w / h, 4),
                "content": content,
                "note": note,
            }
        )

    def fit(img: "Image.Image", w: int, h: int) -> "Image.Image":
        """Center-crop to the target aspect, then scale to exactly (w, h)."""
        from PIL import Image as _I

        target = w / h
        iw, ih = img.size
        if iw / ih > target:
            new_w = int(ih * target)
            box = ((iw - new_w) // 2, 0, (iw - new_w) // 2 + new_w, ih)
        else:
            new_h = int(iw / target)
            box = (0, (ih - new_h) // 2, iw, (ih - new_h) // 2 + new_h)
        return img.crop(box).resize((w, h), _I.LANCZOS)

    # --- the control -------------------------------------------------------
    # 224x224 needs no resize at all: `clip_preprocess` scales the shortest
    # edge to 224 (already 224) and center-crops (a no-op). PIL's resampler
    # never runs, so this row isolates everything that is NOT the resampler.
    emit("ctrl_astronaut_224x224", fit(src("astronaut"), 224, 224),
         "photo", "no-resize control: CLIP's native input size")

    # --- real photographs, native size and aspect --------------------------
    emit("photo_astronaut_512x512", src("astronaut"), "photo", "square photo")
    emit("photo_cat_451x300", src("cat"), "photo", "landscape 1.50")
    # NOT `data.chelsea()`: it is byte-identical to `data.cat()` above
    # (`np.array_equal(data.cat(), data.chelsea())` is True), so it would add
    # a duplicate row to the corpus without adding a measurement.
    emit("photo_motorcycle_741x500", Image.fromarray(data.stereo_motorcycle()[0]), "photo",
         "outdoor scene, mixed frequency")
    emit("photo_coffee_600x400", src("coffee"), "photo", "landscape 1.50")
    emit("photo_rocket_640x427", src("rocket"), "photo", "landscape 1.50")
    emit("photo_hubble_560x488", fit(src("hubble_deep_field"), 560, 488), "photo",
         "dark field, sparse high-contrast points")
    emit("photo_retina_700x700", fit(src("retina"), 700, 700), "photo",
         "large square, heavy downscale")
    emit("photo_ihc_512x512", src("immunohistochemistry"), "photo", "stained tissue, low contrast")

    # --- aspect-ratio spread on photographic content -----------------------
    emit("aspect_astronaut_1280x720", fit(src("astronaut"), 1280, 720), "photo",
         "16:9 — the shape the audit measured failing")
    emit("aspect_astronaut_480x853", fit(src("astronaut"), 480, 853), "photo", "9:16 portrait")
    emit("aspect_rocket_1600x400", fit(src("rocket"), 1600, 400), "photo", "4:1 extreme wide")
    emit("aspect_rocket_400x1600", fit(src("rocket"), 400, 1600), "photo", "1:4 extreme tall")
    emit("aspect_coffee_225x224", fit(src("coffee"), 225, 224), "photo",
         "one pixel off square — the smallest resize that is not the control")

    # --- scale spread ------------------------------------------------------
    emit("scale_hubble_320x279", fit(src("hubble_deep_field"), 320, 279), "photo",
         "heavy downscale before ingest")
    emit("scale_cat_1024x682", fit(src("cat"), 1024, 682), "photo",
         "heavy upscale before ingest (2.3x the 451x300 source)")
    emit("scale_astronaut_100x100", fit(src("astronaut"), 100, 100), "photo",
         "below 224 — upscaled by the preprocessor")

    # --- greyscale ---------------------------------------------------------
    emit("grey_moon_512x512", src("moon"), "greyscale", "smooth greyscale, low frequency")
    emit("grey_coins_384x303", src("coins"), "greyscale", "greyscale objects on texture")
    emit("grey_camera_512x512", src("camera"), "greyscale", "classic greyscale photo")

    # --- high spatial frequency -------------------------------------------
    emit("freq_page_384x191", src("page"), "text", "scanned text page")
    emit("freq_text_448x172", src("text"), "text", "handwritten/printed text strip")
    emit("freq_gravel_512x512", src("gravel"), "texture", "dense high-frequency texture")
    emit("freq_brick_512x512", src("brick"), "texture", "regular high-frequency texture")
    emit("freq_checker_200x200", src("checkerboard"), "synthetic",
         "pathological alternating pixels")

    # --- flat-shaded synthetic: the easy end, incl. the three original
    #     fixtures the bar was previously calibrated on ----------------------
    for name in ("sprite_knight", "chart_revenue", "screenshot_settings"):
        with Image.open(SAMPLE_IMAGES / f"{name}.png") as im:
            emit(f"flat_{name}", im.convert("RGB"), "flat",
                 "flat-shaded Pillow drawing — one of the three original fixtures")

    return rows


def _write_bin(path: Path, vectors) -> None:
    n = len(vectors)
    dim = len(vectors[0])
    if not all(len(v) == dim for v in vectors):
        raise SystemExit(f"mismatched vector dims writing {path}")
    with path.open("wb") as f:
        f.write(struct.pack("<II", n, dim))
        for v in vectors:
            for x in v:
                f.write(struct.pack("<f", float(x)))
    print(f"wrote {path} ({n}x{dim} float32)")


def main() -> int:
    try:
        import skimage  # noqa: F401
    except ImportError:
        print(
            "ERROR: this generator needs scikit-image (its bundled photographs "
            "are the corpus). It is NOT a chunkshop dependency — install it "
            "just to regenerate:  uv pip install scikit-image",
            file=sys.stderr,
        )
        return 2

    rows = _build_corpus()
    MANIFEST.write_text(json.dumps({"images": rows}, indent=2) + "\n")
    print(f"wrote {MANIFEST} ({len(rows)} images)")

    from fastembed import ImageEmbedding

    model = ImageEmbedding("Qdrant/clip-ViT-B-32-vision", threads=1)
    paths = [str(CORPUS_DIR / r["name"]) for r in rows]
    vecs = list(model.embed(paths, batch_size=1))
    _write_bin(REFERENCE, vecs)

    total = sum((CORPUS_DIR / r["name"]).stat().st_size for r in rows)
    print(f"corpus on disk: {total / 1024:.0f} KiB across {len(rows)} images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
