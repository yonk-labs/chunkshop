"""Rung 0 for images: deterministic metadata, no model, no network.

Pillow arrives transitively with fastembed (a core dependency), so this rung
needs no extra and cannot hallucinate. It is always on.
"""

from __future__ import annotations

import re
from pathlib import Path

from chunkshop.sources.derivers.provenance import stamp_provenance
from chunkshop.sources.parsers.result import ParseResult

_SPLIT = re.compile(r"[_\-.]+")


def humanise(stem: str) -> str:
    """`goblin_warrior-02` -> `goblin warrior 02`."""
    return " ".join(part for part in _SPLIT.split(stem) if part)


class ImageMetaDeriver:
    supported_extensions = [
        "png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif",
    ]

    def __init__(self, path_segments: bool = True, segment_depth: int = 3):
        self.path_segments = path_segments
        self.segment_depth = segment_depth

    def derive(self, path: Path) -> ParseResult:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - Pillow ships with fastembed
            raise RuntimeError(
                "image metadata requires Pillow, normally installed with fastembed."
            ) from exc

        with Image.open(path) as img:
            width, height = img.size
            fmt = img.format or path.suffix.lstrip(".").upper()
            mode = img.mode
            has_alpha = mode in ("RGBA", "LA", "PA") or "transparency" in img.info

        words = [humanise(path.stem)]
        if self.path_segments:
            segments = [p for p in path.parent.parts[-self.segment_depth:] if p not in ("/", "")]
            words.extend(humanise(s) for s in segments)

        text = " ".join(w for w in words if w)
        # Fallback: if all segments humanised to empty (e.g., stem is "_"), use format token.
        if not text.strip():
            text = f"{fmt.lower()} image"

        result = ParseResult(
            text=text,
            metadata={
                "width": width,
                "height": height,
                "image_format": fmt,
                "image_mode": mode,
                "aspect_ratio": round(width / height, 4) if height else 0.0,
                "has_alpha": bool(has_alpha),
            },
        )
        return stamp_provenance(
            result,
            rung="image_meta",
            model_id="deterministic",
            model_version="1",
            params={"path_segments": self.path_segments,
                    "segment_depth": self.segment_depth},
        )
