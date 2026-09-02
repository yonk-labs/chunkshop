"""Generate the SC-12 bit-level parity reference vectors for the CLIP TEXT tower.

The VISION tower's fixture moved to `generate_clip_vision_corpus.py` on
2026-09-01. Three images could not characterise the vision residual: the bar
fitted to them was false for photographic content (it failed 21 of 28 rows of a
proper corpus), so the vision side now needs a corpus, a manifest, and a
two-tier bar, which is a different enough job to be a different script.

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
from pathlib import Path

# Single thread: reduction order in ORT is thread-count dependent, and the
# Rust test sets the same vars. Matches `produce_rust_parity_reference.py`.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

from fastembed import TextEmbedding  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"

# Shared with the Rust test — keep the two lists in sync by hand; a mismatch
# shows up immediately as a blown tolerance, not as a silent pass.
TEXTS = [
    "a game sprite or pixel art",          # index 0 — the parity subject
    "quarterly revenue chart, bar graph",  # index 1 — negative control
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

    text_model = TextEmbedding("Qdrant/clip-ViT-B-32-text", cache_dir=CACHE_DIR, threads=1)
    text_vecs = list(text_model.embed(TEXTS, batch_size=1))
    write_bin(FIXTURE_DIR / "clip_text_full_reference.bin", text_vecs)

    for i, v in enumerate(text_vecs):
        norm = sum(float(x) * float(x) for x in v) ** 0.5
        print(f"  text[{i}] dim={len(v)} |v|={norm:.8f} first={float(v[0]):+.8f}")
    print("vision fixtures: see tests/parity/generate_clip_vision_corpus.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
