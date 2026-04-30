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
        vecs = list(self._model.embed(texts, batch_size=self.cfg.batch_size))
        arr = np.stack(vecs).astype(np.float32)
        if arr.shape[1] != self.dim:
            raise ValueError(
                f"model {self.cfg.model_name} produced dim {arr.shape[1]}, "
                f"config says dim={self.dim}"
            )
        return arr
