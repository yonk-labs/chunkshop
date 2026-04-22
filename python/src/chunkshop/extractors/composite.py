"""Composite extractor: chains a list of child extractors and merges their output.

Merge semantics:
- ``tags`` — concatenated in child-declaration order (no dedupe; that's a
  concern for the downstream chunk-level metadata merge if the user wants it).
- ``metadata`` — dict-update in child-declaration order. **Last child wins on
  key collision.** Users should namespace their metadata keys (``entities``,
  ``language``, ``keyphrases``) to avoid silent overwrite.

Failure semantics:
- If any child raises, composite raises ``RuntimeError`` with the child class
  name and the original exception chained via ``from exc`` — no silent swallowing.

No pip extra required on its own; composite's cost is whatever its children
need (install the relevant extras for each child).
"""
from __future__ import annotations

from chunkshop.config import CompositeExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class CompositeExtractor:
    def __init__(self, cfg: Cfg):
        # Lazy import breaks the circular dependency with load_extractor (which
        # in turn imports CompositeExtractor via the package __init__).
        from chunkshop.extractors import load_extractor

        self._children = [load_extractor(child_cfg) for child_cfg in cfg.extractors]

    def extract(self, text: str) -> ExtractResult:
        tags: list[str] = []
        metadata: dict = {}
        for child in self._children:
            child_type = type(child).__name__
            try:
                r = child.extract(text)
            except Exception as exc:  # noqa: BLE001 — intentional, re-raised below
                raise RuntimeError(
                    f"composite extractor: child {child_type} raised: {exc}"
                ) from exc
            tags.extend(r.tags)
            metadata.update(r.metadata)
        return ExtractResult(tags=tags, metadata=metadata)
