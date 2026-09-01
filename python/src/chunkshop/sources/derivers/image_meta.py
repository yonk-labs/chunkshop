"""Rung 0 for images: deterministic metadata, no model, no network.

Pillow arrives transitively with fastembed (a core dependency), so this rung
needs no extra and cannot hallucinate. It is always on.
"""

from __future__ import annotations

import re
from pathlib import Path

from chunkshop.sources.derivers.provenance import stamp_provenance
from chunkshop.sources.parsers.base import ParserError
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

    def derivation_hash(self) -> str:
        from chunkshop.sources.derivers.provenance import derivation_hash

        return derivation_hash(
            "image_meta", "deterministic", "1", "python",
            {"path_segments": self.path_segments, "segment_depth": self.segment_depth},
        )

    def path_text(self, path: Path) -> str:
        """The path-derived prefix of this deriver's output text.

        The single place path -> text material is computed. ``derive()``
        builds its output from it and ``cache_key_extra()`` returns it
        verbatim, so the two can never drift into disagreeing about which
        path material was consumed — a second, key-only re-derivation of
        the same logic is exactly the duplication class that has already
        produced defects on this branch.

        May be empty (a stem of all separators humanises to nothing);
        ``derive()`` owns the non-empty fallback, which depends on the
        image format rather than the path and so is not key material.
        """
        words = [humanise(path.stem)]
        if self.path_segments:
            segments = [
                p for p in path.parent.parts[-self.segment_depth:]
                if p not in ("/", "")
            ]
            words.extend(humanise(s) for s in segments)
        return " ".join(w for w in words if w)

    def cache_key_extra(self, path: Path) -> str | None:
        """This deriver is NOT a pure function of its bytes.

        Its output text comes from the filename (always) and the parent
        segments (only when ``path_segments`` is on), so byte-identical
        images with different names must not share a cache entry.
        Returning ``path_text`` — the exact material consumed under the
        CURRENT config, not the path unconditionally — keeps the key as
        narrow as correctness allows: with ``path_segments`` off the parent
        directory is correctly absent from the key, and a rename that
        leaves the humanised text unchanged (``a-b.png`` -> ``a_b.png``)
        still hits, because the derived text is genuinely identical.
        """
        return self.path_text(path)

    def derive(self, path: Path) -> ParseResult:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - Pillow ships with fastembed
            raise RuntimeError(
                "image metadata requires Pillow, normally installed with fastembed."
            ) from exc

        try:
            with Image.open(path) as img:
                width, height = img.size
                fmt = img.format or path.suffix.lstrip(".").upper()
                mode = img.mode
                has_alpha = mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        except Exception as exc:
            raise ParserError(f"failed to parse image {path}: {exc}") from exc

        text = self.path_text(path)
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
            engine="python",
            params={"path_segments": self.path_segments,
                    "segment_depth": self.segment_depth},
        )
