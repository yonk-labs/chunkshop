from __future__ import annotations
from typing import Protocol
import numpy as np


class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> np.ndarray: ...
