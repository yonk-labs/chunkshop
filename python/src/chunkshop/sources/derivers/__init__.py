"""Media derivers: turn non-text bytes into text plus metadata.

Every deriver's output is stamped with provenance and memoised on
(content_hash, derivation_hash) so nothing is derived twice and nothing is
written without attribution.
"""

from __future__ import annotations

from typing import Any


def load_deriver(cfg: Any):
    """Build a deriver from a config object or an equivalent dict."""
    data = cfg if isinstance(cfg, dict) else cfg.model_dump()
    rung = data.get("rung")

    if rung == "image_meta":
        from chunkshop.sources.derivers.image_meta import ImageMetaDeriver

        return ImageMetaDeriver(
            path_segments=data.get("path_segments", True),
            segment_depth=data.get("segment_depth", 3),
        )
    if rung == "clip_tags":
        from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

        return ClipTagsDeriver(
            vocabulary=data["vocabulary"],
            top_k=data.get("top_k", 3),
            min_score=data.get("min_score", 0.20),
            vision_model=data.get("vision_model", "Qdrant/clip-ViT-B-32-vision"),
            text_model=data.get("text_model", "Qdrant/clip-ViT-B-32-text"),
        )
    raise ValueError(f"unknown rung: {rung!r}")
