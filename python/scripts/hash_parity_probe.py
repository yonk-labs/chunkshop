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

from pathlib import Path

from chunkshop.sources.derivers.cache import DerivationCache
from chunkshop.sources.derivers.provenance import derivation_hash

# Synthetic, never-created root used only so `relative_key_path` can strip
# it back off — `DerivationCache._path` is root-relative by construction,
# and calling the actual production method (not a probe-only
# reimplementation of its hashing) is the point: this proves the SHIPPED
# code agrees with Rust's shipped `relative_key_path`, not two hand-rolled
# stand-ins that could drift from what each language actually runs.
_PROBE_ROOT = Path("/probe-root-unused")


def relative_key_path(content_hash: str, derivation_hash_: str) -> str:
    full = DerivationCache(root=_PROBE_ROOT)._path(content_hash, derivation_hash_)
    return str(full.relative_to(_PROBE_ROOT))


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

    # Correction 2: the cache PATH KEY itself
    # (`sha256(content_hash + "\0" + derivation_hash)`, `key[..2]/key.json`
    # layout) — the most parity-critical line in Task 7. If this differs
    # from Rust's for identical inputs, the two languages silently maintain
    # SEPARATE cache directories even when derivation_hash agrees, which is
    # exactly the scenario `engine` was folded into derivation_hash
    # (Task 6) to prevent. Companion cases, same order, same labels (minus
    # the "rust-"/"python-" prefix), live in
    # rust/chunkshop/examples/hash_parity_probe.rs so the two outputs diff
    # line-for-line.
    print("python-cachekey-simple   ", relative_key_path("c1", "d1"))
    print("python-cachekey-empty    ", relative_key_path("", ""))
    print("python-cachekey-nonascii ", relative_key_path("café", "猫"))
    print(
        "python-cachekey-shalike  ",
        relative_key_path(
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
            "e159c4e35c8cf84439ecaf417477d67316c5607a367f773753ae76c54cdf0d1d",
        ),
    )


if __name__ == "__main__":
    main()
