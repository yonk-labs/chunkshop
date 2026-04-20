from __future__ import annotations
from typing import Protocol

from chunkshop.extractors.result import ExtractResult


class Extractor(Protocol):
    def extract(self, text: str) -> ExtractResult: ...
