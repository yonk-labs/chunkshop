"""Reusable ingestion tool: source -> chunker -> embedder -> extractor -> pgvector table.

The top-level `Pipeline` symbol is lazily imported so light-weight users
(OAuth interfaces, source primitives, test helpers, the connector registry)
don't pay the fastembed/ONNX import cost just to `from chunkshop.* import …`.
"""
from chunkshop.config import CellConfig, load_config

__all__ = ["CellConfig", "load_config", "Pipeline"]


def __getattr__(name):
    # PEP 562 lazy attribute: `chunkshop.Pipeline` (and the equivalent
    # `from chunkshop import Pipeline`) still works, but only triggers the
    # embedder/ONNX import chain when actually accessed — not on every
    # top-level `from chunkshop.X import …`.
    if name == "Pipeline":
        from chunkshop.pipeline import Pipeline as _P
        return _P
    raise AttributeError(f"module 'chunkshop' has no attribute {name!r}")
