"""Generate the SC-12 bit-level parity reference vectors for BOTH CLIP towers.

Sibling of `generate_embedding_reference.py`, which writes a JSON *summary*
(`first8`/`sum`/`l2_norm`) — far too weak for a real per-component tolerance
check on a 512-dim vector. This script writes the FULL vectors in the same
binary layout `scripts/produce_rust_parity_reference.py` already uses for
`embedding_reference_bge_base_int8.bin`, which
`rust/chunkshop/tests/embedding_parity.rs::read_reference` reads:

    u32 n           number of vectors      (little-endian)
    u32 dim         dimension of each      (little-endian)
    f32[n][dim]     row-major flattened    (little-endian)

Run from the repo root:

    cd python && uv run --no-sync python ../tests/parity/generate_clip_full_vectors.py

Each fixture holds TWO vectors, and the second one is load-bearing: it is the
NEGATIVE CONTROL for `rust/chunkshop/tests/clip_embedding_parity.rs`. A parity
test that only ever compares the right vector against the right reference
cannot distinguish "the towers agree" from "the comparison is degenerate"
(e.g. both sides accidentally reading the same buffer). The Rust test asserts
index 0 matches AND index 1 does NOT, so the tolerance has demonstrated
discriminating power.

Regenerate ONLY when intentionally changing a pinned model. These fixtures
come from PYTHON's `fastembed`; the Rust test compares `fastembed-rs` against
them. Never regenerate them from the Rust side — a fixture produced by the
code path it is meant to check proves nothing.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

# Single thread: reduction order in ORT is thread-count dependent, and the
# Rust test sets the same vars. Matches `produce_rust_parity_reference.py`.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

from fastembed import ImageEmbedding, TextEmbedding  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"
IMAGES = REPO_ROOT / "docs" / "samples" / "images"

# Shared with the Rust test — keep the two lists in sync by hand; a mismatch
# shows up immediately as a blown tolerance, not as a silent pass.
TEXTS = [
    "a game sprite or pixel art",          # index 0 — the parity subject
    "quarterly revenue chart, bar graph",  # index 1 — negative control
]
# Image size is load-bearing here, not incidental. `sprite_knight.png` is
# 128x128, so CLIP's 224x224 preprocessing UPSCALES it; the other two are
# larger than 224 and get DOWNSCALED. The PIL-vs-fastembed-rs resize-filter
# divergence behaves very differently in those two directions — see the
# measurements pinned in `rust/chunkshop/tests/clip_embedding_parity.rs`.
IMAGE_PATHS = [
    IMAGES / "sprite_knight.png",       # index 0 — 128x128, upscaled
    IMAGES / "chart_revenue.png",       # index 1 — 320x220, downscaled; also the negative control
    IMAGES / "screenshot_settings.png", # index 2 — 360x260, downscaled
]

# Reuse the cache the Rust side already populated (HF-hub snapshot layout,
# which fastembed-python understands too), so this is a no-download run when
# the models are already present.
CACHE_DIR = str(REPO_ROOT / "rust" / ".fastembed_cache")


def write_bin(path: Path, vectors) -> None:
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
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for p in IMAGE_PATHS:
        if not p.exists():
            print(f"ERROR: missing image fixture {p}", file=sys.stderr)
            return 2

    text_model = TextEmbedding("Qdrant/clip-ViT-B-32-text", cache_dir=CACHE_DIR, threads=1)
    text_vecs = list(text_model.embed(TEXTS, batch_size=1))
    write_bin(FIXTURE_DIR / "clip_text_full_reference.bin", text_vecs)

    vision_model = ImageEmbedding("Qdrant/clip-ViT-B-32-vision", cache_dir=CACHE_DIR, threads=1)
    vision_vecs = list(vision_model.embed([str(p) for p in IMAGE_PATHS], batch_size=1))
    write_bin(FIXTURE_DIR / "clip_vision_full_reference.bin", vision_vecs)

    for label, vecs in (("text", text_vecs), ("vision", vision_vecs)):
        for i, v in enumerate(vecs):
            norm = sum(float(x) * float(x) for x in v) ** 0.5
            print(f"  {label}[{i}] dim={len(v)} |v|={norm:.8f} first={float(v[0]):+.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
