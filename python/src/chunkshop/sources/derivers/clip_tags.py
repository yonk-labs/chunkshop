"""Rung 1 for images: zero-shot tagging with CLIP, keyless and offline.

Embeds a label vocabulary with the CLIP text tower and tags each image by
nearest label. Costs no new dependency — fastembed is already core — and no
API key. Output is TEXT, so it lands in the ordinary text plane alongside
email and PDF and is searched by the hybrid search that already ships.

The vocabulary is the whole game: a generic vocabulary produces generic
tags. It is user-configurable and bakeoff-tunable by design; chunkshop
asserts no default winner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from chunkshop.sources.derivers.image_meta import ImageMetaDeriver
from chunkshop.sources.derivers.provenance import stamp_provenance
from chunkshop.sources.parsers.result import ParseResult

_VISION_DEFAULT = "Qdrant/clip-ViT-B-32-vision"
_TEXT_DEFAULT = "Qdrant/clip-ViT-B-32-text"


class ClipTagsDeriver:
    supported_extensions = ImageMetaDeriver.supported_extensions

    def __init__(
        self,
        vocabulary: Sequence[str],
        top_k: int = 3,
        min_score: float = 0.20,
        vision_model: str = _VISION_DEFAULT,
        text_model: str = _TEXT_DEFAULT,
    ):
        if not vocabulary:
            raise ValueError(
                "ClipTagsDeriver needs a non-empty vocabulary; a generic "
                "vocabulary produces generic tags, so choose it for your corpus."
            )
        self.vocabulary = list(vocabulary)
        self.top_k = top_k
        self.min_score = min_score
        self.vision_model = vision_model
        self.text_model = text_model
        self._meta = ImageMetaDeriver()
        self._vision = None
        self._label_vectors = None

    @staticmethod
    def _model_version() -> str:
        """The installed fastembed version, part of the cache key.

        A fastembed upgrade can change the ONNX weights behind a model id
        (e.g. `Qdrant/clip-ViT-B-32-vision`) without changing that id, which
        would otherwise serve stale cached tags indefinitely — exactly the
        failure the provenance spine exists to prevent. Falls back to
        "unknown" (never raises) so a lookup failure degrades to "always
        re-derive" rather than crashing the deriver.
        """
        import importlib.metadata

        try:
            return importlib.metadata.version("fastembed")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def derivation_hash(self) -> str:
        from chunkshop.sources.derivers.provenance import derivation_hash

        return derivation_hash(
            "clip_tags",
            f"{self.vision_model}+{self.text_model}",
            self._model_version(),
            "python",
            {"vocabulary": self.vocabulary, "top_k": self.top_k,
             "min_score": self.min_score},
        )

    def _ensure_models(self):
        """Lazy: nothing downloads at import or install (NN-6)."""
        if self._vision is not None:
            return
        import numpy as np
        from fastembed import ImageEmbedding, TextEmbedding

        self._vision = ImageEmbedding(self.vision_model)
        text = TextEmbedding(self.text_model)
        vectors = [
            v / np.linalg.norm(v) for v in text.embed(self.vocabulary)
        ]
        self._label_vectors = np.vstack(vectors)

    def derive(self, path: Path) -> ParseResult:
        import numpy as np

        self._ensure_models()
        base = self._meta.derive(path)

        vec = next(iter(self._vision.embed([str(path)])))
        vec = vec / np.linalg.norm(vec)
        scores = self._label_vectors @ vec

        order = np.argsort(scores)[::-1][: self.top_k]
        tags, tag_scores = [], []
        for i in order:
            score = float(scores[i])
            if score >= self.min_score:
                tags.append(self.vocabulary[i])
                tag_scores.append(round(score, 4))

        text = " ".join([base.text, *tags]).strip()
        result = ParseResult(
            text=text,
            metadata={**base.metadata, "clip_tags": tags, "clip_scores": tag_scores},
        )
        return stamp_provenance(
            result,
            rung="clip_tags",
            model_id=f"{self.vision_model}+{self.text_model}",
            model_version=self._model_version(),
            engine="python",
            params={
                "vocabulary": self.vocabulary,
                "top_k": self.top_k,
                "min_score": self.min_score,
            },
        )
