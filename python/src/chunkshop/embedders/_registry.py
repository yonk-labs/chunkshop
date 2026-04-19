"""Register pre-quantized int8 fastembed variants at module import.

Fastembed's registry ships the fp32 BGE variants by default (via qdrant's
`-onnx-q` repos, which are actually fp32 optimized-ONNX). For int8 we point
at community uploads that publish `model_quantized.onnx` alongside the fp32
file. Nomic already has `-Q` built in to fastembed; no registration needed.

The list below is intentionally small — only the three embedders the
factorial experiment uses. Add more as needed.
"""
from __future__ import annotations

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType


_INT8_VARIANTS: list[dict] = [
    {
        "model": "Xenova/bge-small-en-v1.5-int8",
        "dim": 384,
        "pooling": PoolingType.CLS,
        "normalization": True,
        "sources": ModelSource(hf="Xenova/bge-small-en-v1.5"),
        "model_file": "onnx/model_quantized.onnx",
        "description": "bge-small-en-v1.5 pre-quantized to int8 (Xenova upload)",
        "license": "mit",
        "size_in_gb": 0.034,
        "additional_files": [
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "config.json",
        ],
    },
    {
        "model": "Xenova/bge-base-en-v1.5-int8",
        "dim": 768,
        "pooling": PoolingType.CLS,
        "normalization": True,
        "sources": ModelSource(hf="Xenova/bge-base-en-v1.5"),
        "model_file": "onnx/model_quantized.onnx",
        "description": "bge-base-en-v1.5 pre-quantized to int8 (Xenova upload)",
        "license": "mit",
        "size_in_gb": 0.110,
        "additional_files": [
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "config.json",
        ],
    },
]


_REGISTERED = False


def register_int8_variants() -> None:
    """Register chunkshop's known int8 variants if not already registered.

    Idempotent. Safe to call on every import.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    existing = {m["model"] for m in TextEmbedding.list_supported_models()}
    for v in _INT8_VARIANTS:
        if v["model"] in existing:
            continue
        TextEmbedding.add_custom_model(**v)
    _REGISTERED = True


__all__ = ["register_int8_variants"]
