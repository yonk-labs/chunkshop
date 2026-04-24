"""Semantic chunker — split documents at topic shifts detected by embedding
similarity drops between adjacent sentences.

Algorithm:
  1. Split `doc.content` into sentences.
  2. Embed each sentence with a dedicated small boundary model (MiniLM int8 by
     default) or — when `boundary_model='same'` — with the cell's main embedder
     instance to avoid doubling RAM.
  3. Compute cosine distance between adjacent sentence embeddings.
  4. Breakpoints = indices where distance >= percentile-threshold.
  5. Emit one chunk per span, enforcing `min_sentences_per_chunk` (merge forward
     or backward) and `max_chunk_chars` (hard-split on sentence boundary).

Deterministic given same input + same model. No LLM calls.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

from chunkshop.chunkers._sentence_split import load_sentence_splitter
from chunkshop.chunkers.base import Chunk
from chunkshop.config import SemanticChunker as Cfg
from chunkshop.sources.base import Document

log = logging.getLogger(__name__)


class SemanticChunker:
    def __init__(
        self,
        cfg: Cfg,
        *,
        main_embedder_model_name: Optional[str] = None,
        shared_model: Any = None,
    ):
        """Construct a semantic chunker.

        Args:
          cfg: validated `SemanticChunker` config.
          main_embedder_model_name: the cell's main embedder model name. Required
            when `cfg.boundary_model == "same"` so the chunker knows which model
            to reuse. Ignored otherwise.
          shared_model: an already-instantiated `fastembed.TextEmbedding`. When
            provided, the chunker reuses this instance rather than loading its
            own — critical for SC-002 (no double-load when `boundary_model="same"`).
        """
        self.cfg = cfg
        self._split = load_sentence_splitter(cfg.sentence_splitter)
        model_name = cfg.boundary_model
        if model_name == "same":
            if main_embedder_model_name is None:
                raise ValueError(
                    "SemanticChunker(boundary_model='same') requires main_embedder_model_name"
                )
            model_name = main_embedder_model_name
        self._model_name = model_name
        self._model = shared_model  # may be None — lazy-load on first chunk()

    def _get_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            # Register chunkshop's int8 variants idempotently in case callers
            # constructed the chunker before the embedders package was imported.
            from chunkshop.embedders import register_int8_variants
            register_int8_variants()
            self._model = TextEmbedding(model_name=self._model_name, threads=2)
        return self._model

    def chunk(self, doc: Document) -> list[Chunk]:
        if not doc.content or not doc.content.strip():
            return []
        sentences = self._split(doc.content)
        if not sentences:
            return []
        if len(sentences) == 1:
            # One sentence: no boundaries to detect. Still respect max_chars.
            chunks: list[Chunk] = []
            for sub in self._split_if_too_large(sentences[0], self.cfg.max_chunk_chars):
                chunks.append(self._mk_chunk(doc.id, len(chunks), sub))
            return chunks

        model = self._get_model()
        embeddings = np.asarray(list(model.embed(sentences)), dtype=np.float32)
        # Normalize for cosine similarity (bge/minilm usually normalize already;
        # this is idempotent and cheap).
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embeddings = embeddings / norms

        # Cosine distance between adjacent sentences.
        cos_sim = (embeddings[:-1] * embeddings[1:]).sum(axis=1)
        distances = 1.0 - cos_sim

        threshold = float(np.percentile(distances, self.cfg.breakpoint_percentile))
        # Break AFTER sentence i when distances[i] >= threshold.
        breakpoints = [i for i, d in enumerate(distances) if d >= threshold]

        starts = [0]
        for bp in breakpoints:
            starts.append(bp + 1)
        spans = []
        for i, s in enumerate(starts):
            e = starts[i + 1] if i + 1 < len(starts) else len(sentences)
            spans.append((s, e))

        # Enforce min_sentences_per_chunk: merge forward (or backward for last).
        spans = self._merge_small(spans, self.cfg.min_sentences_per_chunk)

        chunks: list[Chunk] = []
        for (s, e) in spans:
            body = " ".join(sentences[s:e]).strip()
            if not body:
                continue
            sub_chunks = self._split_if_too_large(body, self.cfg.max_chunk_chars)
            if len(sub_chunks) > 1:
                log.warning(
                    "semantic chunk exceeded max_chunk_chars=%d (span %d-%d, %d chars); "
                    "hard-split into %d sub-chunks on sentence boundary",
                    self.cfg.max_chunk_chars, s, e, len(body), len(sub_chunks),
                )
            for sub in sub_chunks:
                chunks.append(self._mk_chunk(doc.id, len(chunks), sub))
        return chunks

    def _merge_small(self, spans, min_sents):
        """Merge spans smaller than `min_sents` into neighbors.

        Forward-merges first. If the resulting last span is still too small,
        merges it backward into the previous one.
        """
        if not spans:
            return spans
        merged: list[tuple[int, int]] = []
        for (s, e) in spans:
            if merged and (e - s) < min_sents:
                ps, _ = merged[-1]
                merged[-1] = (ps, e)
            else:
                merged.append((s, e))
        # If the first span ended up below threshold (couldn't forward-merge
        # because nothing was to its left), pull the next span into it.
        if len(merged) > 1 and (merged[0][1] - merged[0][0]) < min_sents:
            merged[0] = (merged[0][0], merged[1][1])
            merged.pop(1)
        # If the final span is still below threshold (it was forward-merged but
        # is alone at the end), back-merge into the previous.
        if len(merged) > 1 and (merged[-1][1] - merged[-1][0]) < min_sents:
            ps, _ = merged[-2]
            _, pe = merged[-1]
            merged[-2] = (ps, pe)
            merged.pop()
        return merged

    def _split_if_too_large(self, body: str, max_chars: int) -> list[str]:
        """Hard-split a body that exceeds max_chars on sentence boundaries."""
        if len(body) <= max_chars:
            return [body]
        sents = self._split(body)
        if not sents:
            # No sentence boundaries — fall back to character slicing.
            return [body[i:i + max_chars] for i in range(0, len(body), max_chars)]
        out: list[str] = []
        cur = ""
        for s in sents:
            candidate = (cur + " " + s).strip() if cur else s
            if len(candidate) > max_chars and cur:
                out.append(cur.strip())
                cur = s
            else:
                cur = candidate
        if cur:
            # One final guard: if a single sentence is longer than max_chars,
            # char-slice it.
            if len(cur) > max_chars:
                for i in range(0, len(cur), max_chars):
                    out.append(cur[i:i + max_chars])
            else:
                out.append(cur.strip())
        return out

    def _mk_chunk(self, doc_id: str, seq: int, text: str) -> Chunk:
        return Chunk(
            doc_id=doc_id,
            seq_num=seq,
            original_content=text,
            embedded_content=text,
            metadata={"strategy": "semantic"},
        )
