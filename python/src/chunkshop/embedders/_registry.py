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
    {
        # Used by SemanticChunker as the dedicated small boundary model.
        # MiniLM uses mean pooling (unlike BGE which uses CLS).
        "model": "sentence-transformers/all-MiniLM-L6-v2-int8",
        "dim": 384,
        "pooling": PoolingType.MEAN,
        "normalization": True,
        "sources": ModelSource(hf="Xenova/all-MiniLM-L6-v2"),
        "model_file": "onnx/model_quantized.onnx",
        "description": "all-MiniLM-L6-v2 pre-quantized to int8 (Xenova upload)",
        "license": "apache-2.0",
        "size_in_gb": 0.022,
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


def register_byo_model(cfg) -> None:
    """Register a YAML-driven BYO model with fastembed at config-load time.

    Called from `load_embedder` when `cfg.hf_repo` is set. Idempotent: skips
    registration if `cfg.model_name` is already known to fastembed.

    Two-step download because fastembed's cache is per-`sources.hf` repo —
    if a snapshot for the repo already exists (e.g. from an earlier
    different model_file registration), fastembed reuses it and does NOT
    fetch the file we're registering. We pre-fetch via `huggingface_hub`
    first so the file is on disk before fastembed looks for it.

    Translates the YAML's `pooling: cls|mean` to fastembed's `PoolingType`
    enum, defaults `normalization=True` (the right answer for retrieval
    embeddings — chunkshop's only use case), and uses the user-supplied
    `additional_files` list.
    """
    existing = {m["model"] for m in TextEmbedding.list_supported_models()}
    if cfg.model_name in existing:
        return
    pooling_map = {"cls": PoolingType.CLS, "mean": PoolingType.MEAN}
    if cfg.pooling not in pooling_map:
        raise ValueError(
            f"embedder.pooling must be 'cls' or 'mean', got {cfg.pooling!r}"
        )

    # Pre-fetch the ONNX file + tokenizer config files into the same
    # fastembed-cache repo dir fastembed will look in. Use FASTEMBED_CACHE
    # if set, else fastembed's default of ~/.cache/fastembed (its own
    # convention). The huggingface_hub call lays down both blobs/ and
    # snapshots/ properly so fastembed sees them as natively-fetched.
    import os
    from huggingface_hub import hf_hub_download

    # fastembed's default cache root is <tempdir>/fastembed_cache (/tmp/fastembed_cache
    # on Linux) — see fastembed.common.model_management. Mirror that exact path
    # so our hf_hub_download lays files into the same blobs/snapshots tree
    # fastembed will look in.
    import tempfile
    cache_root = os.environ.get(
        "FASTEMBED_CACHE_PATH",
        os.path.join(tempfile.gettempdir(), "fastembed_cache"),
    )
    files_to_fetch = [cfg.onnx_path] + list(cfg.additional_files)
    for path_in_repo in files_to_fetch:
        try:
            hf_hub_download(
                repo_id=cfg.hf_repo,
                filename=path_in_repo,
                cache_dir=cache_root,
            )
        except Exception as exc:
            # tokenizer_config.json is optional in some repos; everything
            # else is required. Re-raise unless this is a known-optional
            # file that's safe to skip.
            if path_in_repo in {"tokenizer_config.json", "special_tokens_map.json"}:
                continue
            raise RuntimeError(
                f"failed to fetch {path_in_repo!r} from {cfg.hf_repo!r}: {exc}. "
                f"Verify the file exists at https://huggingface.co/{cfg.hf_repo}/tree/main"
            ) from exc

    TextEmbedding.add_custom_model(
        model=cfg.model_name,
        dim=cfg.dim,
        pooling=pooling_map[cfg.pooling],
        normalization=True,
        sources=ModelSource(hf=cfg.hf_repo),
        model_file=cfg.onnx_path,
        description=f"BYO model registered at config-load: {cfg.model_name}",
        license="user-supplied",
        size_in_gb=0.0,  # fastembed only uses this for download UX
        additional_files=list(cfg.additional_files),
    )


__all__ = ["register_int8_variants", "register_byo_model"]
