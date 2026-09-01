#!/usr/bin/env python3
"""Cross-language check: does Python's derivation_hash agree with Rust's for
identical inputs? The cache key IS the provenance record, so a divergence
here means the two implementations silently maintain separate caches.

Companion to rust/chunkshop/examples/hash_parity_probe.rs — same cases, same
order, same labels (minus the "rust-"/"python-" prefix), so the two outputs
can be diffed line-for-line.

Usage:
    cd python && uv run --no-sync python scripts/hash_parity_probe.py
"""
from __future__ import annotations

from chunkshop.sources.derivers.provenance import derivation_hash


def main() -> None:
    params = {"top_k": 3, "min_score": 0.2, "vocabulary": ["a cat", "a dog"]}
    print(
        "python-clip  ",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", params),
    )
    print(
        "python-empty ",
        derivation_hash("image_meta", "deterministic", "1", "rust", {}),
    )
    # key-order independence: same content, different literal order
    reordered = {"vocabulary": ["a cat", "a dog"], "min_score": 0.2, "top_k": 3}
    print(
        "python-reord ",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", reordered),
    )

    # FIX 1 review cases: does canonicalization agree with Rust beyond plain
    # ASCII? Python's json.dumps defaults to ensure_ascii=True (emitting
    # \uXXXX escapes); serde_json always emits raw UTF-8. All cases below
    # share the same (rung, model, version, engine) tuple as "python-clip"
    # so any divergence can only come from params canonicalization, not the
    # other hash inputs.
    non_ascii = {"top_k": 3, "vocabulary": ["café", "猫"]}
    print(
        "python-nonascii",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", non_ascii),
    )

    quote = {"text": 'she said "hello"'}
    print(
        "python-quote   ",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", quote),
    )

    backslash = {"path": "C:\\Users\\test"}
    print(
        "python-backslash",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", backslash),
    )

    control = {"text": "line one\nline two\ttabbed"}
    print(
        "python-control ",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", control),
    )

    emoji = {"text": "launch \U0001F680 party \U0001F389"}
    print(
        "python-emoji   ",
        derivation_hash("clip_tags", "Qdrant/clip-ViT-B-32", "1.2.3", "fastembed-rs", emoji),
    )


if __name__ == "__main__":
    main()
