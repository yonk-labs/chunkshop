from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

from chunkshop.config import FastembedEmbedder as Cfg


def _purge_incomplete_model_cache() -> bool:
    """Delete HF-cache model dirs that hold a ``*.incomplete`` blob.

    An interrupted first download leaves a 0-byte ``*.incomplete`` blob in the
    fastembed/HF cache; fastembed then treats the snapshot as present and skips
    the download, and ONNX Runtime fails ``NO_SUCHFILE`` on every later init
    until the dir is wiped by hand. Wiping only the poisoned model dir(s) lets
    the next construction re-download cleanly. See issue #80.

    Mirrors fastembed's own cache-root convention (``FASTEMBED_CACHE_PATH`` or
    ``<tempdir>/fastembed_cache``). Returns True if anything was deleted.
    """
    cache_root = Path(
        os.environ.get(
            "FASTEMBED_CACHE_PATH",
            os.path.join(tempfile.gettempdir(), "fastembed_cache"),
        )
    )
    if not cache_root.is_dir():
        return False
    purged = False
    for model_dir in cache_root.glob("models--*"):
        if any(model_dir.rglob("*.incomplete")):
            shutil.rmtree(model_dir, ignore_errors=True)
            purged = True
    return purged


class FastembedProvider:
    """Embedder backed by fastembed.TextEmbedding (ONNX runtime + HF tokenizers).

    First use of a given model_name downloads the ONNX files to the fastembed
    cache (~/.cache/fastembed by default). Subsequent uses are local.
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.dim = cfg.dim
        # Cumulative wall time spent inside `embed()` calls. Used by run_cell
        # and the bakeoff to break out the embedder's portion of total ingest
        # wall time — answers "is this combo slow because of the embedder or
        # because of the chunker/sink?"
        self.embed_seconds: float = 0.0
        # threads=N caps ORT intra_op_num_threads at session init. Without this,
        # fastembed auto-detects and creates a pool sized to all cores, which
        # thrashes badly when running 4 workers concurrently on a shared box.
        kwargs = {"model_name": cfg.model_name}
        if cfg.threads is not None:
            kwargs["threads"] = cfg.threads
        try:
            self._model = TextEmbedding(**kwargs)
        except Exception:
            # A download interrupted mid-fetch poisons the cache (see
            # _purge_incomplete_model_cache). If we find such a partial blob,
            # wipe it and retry the download once; otherwise this was a real
            # failure, so re-raise it unchanged.
            if not _purge_incomplete_model_cache():
                raise
            self._model = TextEmbedding(**kwargs)

        # Normalize tokenizer padding to BatchLongest. Some HF-uploaded
        # tokenizer.json files (notably the Xenova sentence-transformers
        # conversions) ship with Fixed=128 padding. fastembed-py's loader
        # only enables padding `if not tokenizer.padding`, so it leaves
        # Fixed=128 in place. For inputs longer than 128 tokens, the
        # tokenizer keeps natural lengths while shorter inputs pad to 128 —
        # producing inhomogeneous batch tensors that fail at np.array().
        # Switching to BatchLongest pads every element of a batch to the
        # batch's longest token sequence and works for all input lengths.
        # Skip silently if the inner tokenizer isn't available — some
        # fastembed model classes don't expose .tokenizer the same way.
        inner = getattr(self._model, "model", None)
        tok = getattr(inner, "tokenizer", None) if inner is not None else None
        if tok is not None and tok.padding is not None:
            try:
                # Re-enable padding without a fixed length → BatchLongest.
                tok.enable_padding(
                    pad_id=tok.padding["pad_id"],
                    pad_token=tok.padding["pad_token"],
                    pad_type_id=tok.padding.get("pad_type_id", 0),
                    direction=tok.padding.get("direction", "right"),
                )
            except Exception:
                # Don't fail the cell on a tokenizer-quirk fix; the original
                # configuration may already be correct for this model.
                pass

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        t0 = time.perf_counter()
        vecs = list(self._model.embed(texts, batch_size=self.cfg.batch_size))
        arr = np.stack(vecs).astype(np.float32)
        self.embed_seconds += time.perf_counter() - t0
        if arr.shape[1] != self.dim:
            raise ValueError(
                f"model {self.cfg.model_name} produced dim {arr.shape[1]}, "
                f"config says dim={self.dim}"
            )
        return arr
