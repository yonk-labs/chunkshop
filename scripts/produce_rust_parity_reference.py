"""Produce reference embedding vectors for the Rust bit-exact parity test.

Run once from the chunkshop repo root with `uv run --project python python
scripts/produce_rust_parity_reference.py`. Writes the input fixture and a
binary file of float32 reference vectors that the Rust integration test
(rust/chunkshop/tests/embedding_parity.rs) compares against.

Format of the .bin file (little-endian throughout):
    u32 n           number of vectors
    u32 dim         dimension of each vector
    f32[n][dim]     row-major flattened vectors

Re-running this script regenerates the fixtures byte-for-byte (deterministic
because we use OMP_NUM_THREADS=1 and batch_size=1).
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path


# Single thread for deterministic output across machines.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

from chunkshop.embedders._registry import register_int8_variants  # noqa: E402
from fastembed import TextEmbedding  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "rust" / "chunkshop" / "tests" / "parity-fixtures"

# Five inputs chosen to exercise the embedder: short, longer, ASCII, mixed
# punctuation, and a sentence with internal commas. Order is load-bearing —
# the binary file is read in this order on the Rust side.
INPUTS = [
    "Hello world.",
    "Chunkshop reference text 1.",
    "Numerical reproducibility matters.",
    "Empty-ish input handling check.",
    "The quick brown fox jumps over the lazy dog.",
]


def main() -> int:
    register_int8_variants()
    model = TextEmbedding(
        model_name="Xenova/bge-base-en-v1.5-int8",
        threads=1,
    )

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    inputs_path = FIXTURE_DIR / "embedding_inputs.txt"
    inputs_path.write_text("\n".join(INPUTS) + "\n", encoding="utf-8")

    vectors = list(model.embed(INPUTS, batch_size=1))
    n = len(vectors)
    dim = len(vectors[0])
    if not all(len(v) == dim for v in vectors):
        print("ERROR: mismatched vector dims", file=sys.stderr)
        return 2

    bin_path = FIXTURE_DIR / "embedding_reference_bge_base_int8.bin"
    with bin_path.open("wb") as f:
        f.write(struct.pack("<II", n, dim))
        for v in vectors:
            for x in v.astype("float32").tolist():
                f.write(struct.pack("<f", float(x)))

    print(f"wrote {inputs_path} ({len(INPUTS)} lines)")
    print(f"wrote {bin_path} ({n}x{dim} float32)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
