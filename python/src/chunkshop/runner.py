"""Single-cell runner: wires source -> chunker -> embedder -> extractor -> sink."""
from __future__ import annotations
import logging
import os
import time
import traceback

logger = logging.getLogger(__name__)
from dataclasses import dataclass, replace as _replace
from pathlib import Path
from typing import Optional

from chunkshop.chunkers import load_chunker
from chunkshop.config import CellConfig
from chunkshop.embedders import load_embedder
from chunkshop.extractors import load_extractor
from chunkshop.framers import load_framer
from chunkshop.sinks import load_sink
from chunkshop.sources import load_source


@dataclass
class CellResult:
    cell_name: str
    docs_processed: int
    chunks_written: int
    wall_seconds: float
    # Wall time spent inside the embedder's `embed()` calls. Subset of
    # `wall_seconds`; the rest covers chunking, extraction, sink writes,
    # source iteration. Helps the bakeoff distinguish "this combo is slow
    # because of the embedder" from "this combo is slow for other reasons".
    # 0.0 when the run errored before embedding started.
    embed_seconds: float = 0.0
    error: Optional[str] = None


def _log(msg: str, log_path: Optional[Path]) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    logger.info("%s", line)
    if log_path:
        with log_path.open("a") as f:
            f.write(line + "\n")


def run_cell(cfg: CellConfig) -> CellResult:
    # Cap CPU threads before any embedder loads ONNX. These env vars must be
    # set before numpy / ONNX / BLAS libs are imported — most read them once at
    # module load. The embedder's own `threads` config also caps ORT's
    # intra_op_num_threads via SessionOptions (see FastembedProvider).
    n = str(cfg.runtime.omp_num_threads)
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, n)

    log_path = Path(cfg.runtime.log_path) if cfg.runtime.log_path else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    _log(f"cell {cfg.cell_name} starting", log_path)
    try:
        source = load_source(cfg.source)
        framer = load_framer(cfg.framer)
        embedder = load_embedder(cfg.embedder)
        # SemanticChunker(boundary_model="same") reuses the main embedder's
        # TextEmbedding instance to avoid doubling RAM (SC-002). Grab it via the
        # private attr — FastembedProvider holds its model at `_model`. This is a
        # deliberate coupling between runner and provider; cheaper than a public
        # accessor that only one caller would use.
        shared_boundary_model = getattr(embedder, "_model", None)
        chunker = load_chunker(
            cfg.chunker,
            main_embedder=cfg.embedder,
            shared_boundary_model=shared_boundary_model,
        )
        extractor = load_extractor(cfg.extractor)
        sink = load_sink(cfg.target, embed_dim=cfg.embedder.dim)

        _log("creating target table", log_path)
        sink.create_table()

        docs_processed = 0
        chunks_written = 0
        limit = cfg.runtime.doc_limit
        heartbeat = cfg.runtime.heartbeat_every

        for raw in source.iter_documents():
            if limit is not None and docs_processed >= limit:
                break
            for doc in framer.frame(raw):
                chunks = chunker.chunk(doc)
                if not chunks:
                    continue
                doc_record_metadata = dict(doc.metadata or {})
                if cfg.target.documents.enabled and getattr(cfg.extractor, "type", None) == "lede_report":
                    doc_extract = extractor.extract(doc.content)
                    doc_record_metadata = {**doc_record_metadata, **doc_extract.metadata}
                texts = [c.embedded_content for c in chunks]
                embeddings = embedder.embed(texts)
                results = [extractor.extract(c.original_content) for c in chunks]
                tags = [r.tags for r in results]
                # Layered metadata merge with chunker-wins semantics:
                #   1. doc.metadata — framer-produced (framer, frame_seq)
                #   2. r.metadata   — extractor-produced
                #   3. c.metadata   — chunker-produced (wins on collision)
                # So chunker keys (strategy, heading, section_part) override lower
                # layers, and framer/extractor keys survive when not overridden.
                doc_meta = doc.metadata or {}
                chunks = [
                    _replace(c, metadata={**doc_meta, **r.metadata, **c.metadata})
                    for c, r in zip(chunks, results)
                ]
                write_doc_record = getattr(sink, "write_document_record", None)
                if write_doc_record is not None:
                    write_doc_record(
                        doc_id=doc.id,
                        title=doc.title,
                        content=doc.content,
                        metadata=doc_record_metadata,
                        chunk_count=len(chunks),
                    )
                sink.write_document(doc.id, chunks, embeddings, tags)
                chunks_written += len(chunks)
            # `docs_processed` counts RAW docs (what the source yielded), not framed docs.
            docs_processed += 1
            if docs_processed % heartbeat == 0:
                elapsed = time.time() - start
                _log(
                    f"heartbeat docs={docs_processed} chunks={chunks_written} elapsed={elapsed:.1f}s",
                    log_path,
                )

        wall = time.time() - start
        _log(
            f"cell {cfg.cell_name} DONE docs={docs_processed} chunks={chunks_written} wall={wall:.1f}s",
            log_path,
        )
        return CellResult(
            cell_name=cfg.cell_name,
            docs_processed=docs_processed,
            chunks_written=chunks_written,
            wall_seconds=wall,
            embed_seconds=getattr(embedder, "embed_seconds", 0.0),
            error=None,
        )
    except Exception as exc:
        wall = time.time() - start
        tb = traceback.format_exc()
        _log(f"cell {cfg.cell_name} FAILED: {exc}\n{tb}", log_path)
        return CellResult(
            cell_name=cfg.cell_name,
            docs_processed=0,
            chunks_written=0,
            wall_seconds=wall,
            error=str(exc),
        )
