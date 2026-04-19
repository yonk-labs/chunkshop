from chunkshop.config import EmbedderConfig, FastembedEmbedder as FastCfg
from chunkshop.embedders.base import Embedder
from chunkshop.embedders.fastembed_provider import FastembedProvider


def load_embedder(cfg: EmbedderConfig) -> Embedder:
    if isinstance(cfg, FastCfg):
        return FastembedProvider(cfg)
    raise ValueError(f"unknown embedder type: {type(cfg).__name__}")


__all__ = ["Embedder", "load_embedder"]
