from __future__ import annotations

import numpy as np
from fastembed import TextEmbedding

from chunkshop.config import FastembedEmbedder as Cfg


class FastembedProvider:
    """Embedder backed by fastembed.TextEmbedding (ONNX runtime + HF tokenizers).

    First use of a given model_name downloads the ONNX files to the fastembed
    cache (~/.cache/fastembed by default). Subsequent uses are local.
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.dim = cfg.dim
        # threads=N caps ORT intra_op_num_threads at session init. Without this,
        # fastembed auto-detects and creates a pool sized to all cores, which
        # thrashes badly when running 4 workers concurrently on a shared box.
        kwargs = {"model_name": cfg.model_name}
        if cfg.threads is not None:
            kwargs["threads"] = cfg.threads
        self._model = TextEmbedding(**kwargs)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        vecs = list(self._model.embed(texts, batch_size=self.cfg.batch_size))
        arr = np.stack(vecs).astype(np.float32)
        if arr.shape[1] != self.dim:
            raise ValueError(
                f"model {self.cfg.model_name} produced dim {arr.shape[1]}, "
                f"config says dim={self.dim}"
            )
        return arr
