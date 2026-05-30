from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import numpy as np

from chunkshop.config import OpenAIEmbedder as Cfg


class OpenAIEmbeddingProvider:
    """Embedder backed by a remote OpenAI-compatible /v1/embeddings endpoint.

    Network-bound, opt-in alternative to FastembedProvider. Targets OpenAI,
    Azure OpenAI, Voyage, Mistral, Together, or local servers (TEI / vLLM /
    Ollama) via base_url + model (+ optional api_key_env). Stdlib HTTP only —
    no extra dependency.
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.dim = cfg.dim
        self.embed_seconds: float = 0.0
        self._url = cfg.base_url.rstrip("/") + "/embeddings"
        self._headers = {"Content-Type": "application/json"}
        if cfg.api_key_env is not None:
            key = os.environ.get(cfg.api_key_env)
            if not key:
                raise ValueError(
                    f"embedder.api_key_env={cfg.api_key_env!r} but that "
                    f"environment variable is unset or empty"
                )
            self._headers["Authorization"] = f"Bearer {key}"

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        t0 = time.perf_counter()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.cfg.batch_size):
            vectors.extend(
                self._embed_batch(texts[start : start + self.cfg.batch_size])
            )
        arr = np.asarray(vectors, dtype=np.float32)
        self.embed_seconds += time.perf_counter() - t0
        if arr.ndim != 2 or arr.shape[1] != self.dim:
            got = arr.shape[1] if arr.ndim == 2 else "?"
            raise ValueError(
                f"embedder model {self.cfg.model!r} produced dim {got}, "
                f"config says dim={self.dim}"
            )
        return arr

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        body = json.dumps(
            {"model": self.cfg.model, "input": batch, "encoding_format": "float"}
        ).encode("utf-8")
        payload = self._post(body)
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ValueError(
                f"embeddings endpoint returned no data for {len(batch)} inputs"
            )
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in ordered]

    def _post(self, body: bytes) -> dict:
        req = urllib.request.Request(
            self._url, data=body, headers=self._headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


__all__ = ["OpenAIEmbeddingProvider"]
