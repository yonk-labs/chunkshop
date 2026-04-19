from __future__ import annotations
from typing import Protocol


class Extractor(Protocol):
    def extract(self, text: str) -> list[str]: ...
