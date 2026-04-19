"""Verify chunkshop registers its int8 variants with fastembed at import time."""
from fastembed import TextEmbedding

import chunkshop.embedders  # noqa: F401  triggers register_int8_variants()


_EXPECTED_INT8 = [
    ("Xenova/bge-small-en-v1.5-int8", 384),
    ("Xenova/bge-base-en-v1.5-int8", 768),
    # nomic -Q ships with fastembed itself; we don't register it, but the
    # chunkshop contract says these three names all resolve.
    ("nomic-ai/nomic-embed-text-v1.5-Q", 768),
]


def test_int8_variants_are_registered():
    registry = {m["model"]: m for m in TextEmbedding.list_supported_models()}
    for name, expected_dim in _EXPECTED_INT8:
        assert name in registry, f"{name!r} missing from fastembed registry"
        assert registry[name]["dim"] == expected_dim, (
            f"{name!r} registered with dim={registry[name]['dim']}, "
            f"expected {expected_dim}"
        )


def test_registration_is_idempotent():
    # Calling register a second time must not double-register or error.
    from chunkshop.embedders._registry import register_int8_variants
    before = len(TextEmbedding.list_supported_models())
    register_int8_variants()
    register_int8_variants()
    after = len(TextEmbedding.list_supported_models())
    assert before == after
