"""Reusable ingestion tool: source -> chunker -> embedder -> extractor -> pgvector table."""
from chunkshop.config import CellConfig, load_config
from chunkshop.pipeline import Pipeline

__all__ = ["CellConfig", "load_config", "Pipeline"]
