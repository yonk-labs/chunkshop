"""Generate the SC-12 bit-level parity reference vectors.

These are the ground truth the Rust port is checked against: same fixture text,
same pinned model IDs, vectors must agree within float tolerance. Regenerate with:

    cd python && .venv/bin/python ../tests/parity/generate_embedding_reference.py

Regenerate ONLY when intentionally changing a pinned model — a silent change here
would make a real Rust divergence look like a passing test. fastembed returns
L2-normalised vectors, so |v| == 1.0 and cosine == dot product.
"""
import json, hashlib
from fastembed import TextEmbedding, ImageEmbedding
import chunkshop.embedders._registry  # noqa: F401  -- registers the int8 BGE variants
import numpy as np

FIXTURE_TEXT = "a game sprite or pixel art"
out = {"fixture_text": FIXTURE_TEXT, "models": {}}

for name, model_id in [
    ("text_bge_int8", "Xenova/bge-base-en-v1.5-int8"),
    ("clip_text",     "Qdrant/clip-ViT-B-32-text"),
]:
    m = TextEmbedding(model_id)
    v = np.asarray(next(iter(m.embed([FIXTURE_TEXT]))), dtype=np.float64)
    out["models"][name] = {
        "model_id": model_id,
        "dim": int(v.shape[0]),
        "l2_norm": float(np.linalg.norm(v)),
        "first8": [round(float(x), 8) for x in v[:8]],
        "sum": round(float(v.sum()), 8),
        "sha256_of_rounded6": hashlib.sha256(
            json.dumps([round(float(x), 6) for x in v]).encode()).hexdigest()[:16],
    }
    print(f"{name:14} dim={v.shape[0]:4d} |v|={np.linalg.norm(v):.6f} first={v[0]:+.6f},{v[1]:+.6f}")

print(json.dumps(out, indent=2)[:0])  # keep stdout tidy
import pathlib
pathlib.Path(__file__).with_name("embedding-reference.json").write_text(json.dumps(out, indent=2))
print("\nwrote parity-reference.json")
