"""Single-cell runner: wires source -> chunker -> embedder -> extractor -> sink."""
from __future__ import annotations
import os
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from chunkshop.chunkers import load_chunker
from chunkshop.config import CellConfig
from chunkshop.embedders import load_embedder
from chunkshop.extractors import load_extractor
from chunkshop.sink import PgVectorSink
from chunkshop.sources import load_source


@dataclass
class CellResult:
    cell_name: str
    docs_processed: int
    chunks_written: int
    wall_seconds: float
    error: Optional[str] = None


def _log(msg: str, log_path: Optional[Path]) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
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
        chunker = load_chunker(cfg.chunker)
        embedder = load_embedder(cfg.embedder)
        extractor = load_extractor(cfg.extractor)
        sink = PgVectorSink(cfg.target, embed_dim=cfg.embedder.dim)

        _log("creating target table", log_path)
        sink.create_table()

        docs_processed = 0
        chunks_written = 0
        limit = cfg.runtime.doc_limit
        heartbeat = cfg.runtime.heartbeat_every

        for doc in source.iter_documents():
            if limit is not None and docs_processed >= limit:
                break
            chunks = chunker.chunk(doc)
            if not chunks:
                docs_processed += 1
                continue
            texts = [c.embedded_content for c in chunks]
            embeddings = embedder.embed(texts)
            tags = [extractor.extract(c.original_content) for c in chunks]
            sink.write_document(doc.id, chunks, embeddings, tags)
            chunks_written += len(chunks)
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
