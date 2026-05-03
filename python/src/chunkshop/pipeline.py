"""Pipeline — chunkshop as a library. The host app drives ingestion.

Skips the YAML-defined source. The YAML still defines chunker, embedder,
extractor, framer, and target — `Pipeline` builds those once and exposes
`ingest_text(doc_id, text, metadata)` so calling code (a Python service,
queue worker, webhook handler) can push individual documents through the
pipeline without spawning a subprocess per doc.

Use `chunkshop.Pipeline.from_yaml(path)`. The YAML's `source.type` must be
`inline` — any other source raises at construction so the YAML's intent
matches the call site's behavior.

`delete_document(doc_id)` removes all chunks for a doc — paired with
`target.delete_orphans` and an explicit "this doc is gone" signal from your
app, this gives you full insert / update / delete semantics without CDC.
"""
from __future__ import annotations
import os
from dataclasses import replace as _replace
from pathlib import Path
from typing import Optional

import yaml

from chunkshop.chunkers import load_chunker
from chunkshop.config import CellConfig, InlineSource
from chunkshop.embedders import load_embedder
from chunkshop.extractors import load_extractor
from chunkshop.framers import load_framer
from chunkshop.sinks import load_sink
from chunkshop.sources.base import Document


class Pipeline:
    """In-process chunk-embed-store pipeline driven by the host app."""

    def __init__(self, cfg: CellConfig):
        if not isinstance(cfg.source, InlineSource):
            raise ValueError(
                f"Pipeline requires source.type='inline', got {cfg.source.type!r}. "
                f"Use chunkshop.runner.run_cell(cfg) for source-driven configs."
            )
        # Same OMP/BLAS thread-cap as runner.run_cell — must run before the
        # embedder loads ONNX. No-op if vars are already set.
        n = str(cfg.runtime.omp_num_threads)
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ.setdefault(var, n)

        self.cfg = cfg
        self._framer = load_framer(cfg.framer)
        self._embedder = load_embedder(cfg.embedder)
        shared_boundary_model = getattr(self._embedder, "_model", None)
        self._chunker = load_chunker(
            cfg.chunker,
            main_embedder=cfg.embedder,
            shared_boundary_model=shared_boundary_model,
        )
        self._extractor = load_extractor(cfg.extractor)
        self._sink = load_sink(cfg.target, embed_dim=cfg.embedder.dim)
        self._sink.create_table()

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Pipeline":
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls(CellConfig(**data))

    def ingest_text(
        self,
        doc_id: str,
        text: str,
        metadata: Optional[dict] = None,
        title: Optional[str] = None,
    ) -> int:
        """Run one document through framer → chunker → embedder → extractor → sink.

        Returns the number of chunks written.
        """
        doc = Document(id=doc_id, content=text, title=title, metadata=metadata or {})
        return self.ingest_document(doc)

    def ingest_document(self, doc: Document) -> int:
        chunks_written = 0
        for framed in self._framer.frame(doc):
            chunks = self._chunker.chunk(framed)
            if not chunks:
                continue
            texts = [c.embedded_content for c in chunks]
            embeddings = self._embedder.embed(texts)
            results = [self._extractor.extract(c.original_content) for c in chunks]
            tags = [r.tags for r in results]
            doc_meta = framed.metadata or {}
            chunks = [
                _replace(c, metadata={**doc_meta, **r.metadata, **c.metadata})
                for c, r in zip(chunks, results)
            ]
            self._sink.write_document(framed.id, chunks, embeddings, tags)
            chunks_written += len(chunks)
        return chunks_written

    def delete_document(self, doc_id: str) -> int:
        """Remove every chunk for a doc_id, scoped to this pipeline's source_tag.

        Returns the number of rows deleted. Scoping by source_tag matches the
        write-once provenance contract: a Pipeline configured for source_tag X
        cannot delete rows owned by source_tag Y.
        """
        cfg = self.cfg.target
        fq = self._sink._fq()  # PgSink exposes _fq()
        backend = self._sink.backend
        with backend.connect() as conn, conn.cursor() as cur:
            if cfg.source_tag:
                cur.execute(
                    f"DELETE FROM {fq} WHERE doc_id = %s AND source = %s",
                    (doc_id, cfg.source_tag),
                )
            else:
                cur.execute(f"DELETE FROM {fq} WHERE doc_id = %s", (doc_id,))
            deleted = cur.rowcount
            conn.commit()
        return deleted

    def count_docs(self) -> int:
        return self._sink.count_docs()
