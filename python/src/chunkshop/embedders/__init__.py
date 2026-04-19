from chunkshop.config import EmbedderConfig, FastembedEmbedder as FastCfg
from chunkshop.embedders._registry import register_int8_variants
from chunkshop.embedders.base import Embedder
from chunkshop.embedders.fastembed_provider import FastembedProvider

# Register chunkshop's pre-quantized int8 variants so users can reference them
# in YAML (e.g. model_name: "Xenova/bge-small-en-v1.5-int8"). Idempotent.
register_int8_variants()


def load_embedder(cfg: EmbedderConfig) -> Embedder:
    if isinstance(cfg, FastCfg):
        return FastembedProvider(cfg)
    raise ValueError(f"unknown embedder type: {type(cfg).__name__}")


__all__ = ["Embedder", "load_embedder", "register_int8_variants"]
