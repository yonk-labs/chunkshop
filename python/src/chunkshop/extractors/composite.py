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
    # Composite forwards chunk-context kwargs to any child that declares
    # ``accepts_chunk_context``. Setting it on the composite itself tells the
    # runner "yes, hand me source_path / language" and the runtime
    # ``extract()`` below splices them through only to children that opted in
    # — so a composite mixing ``code_relationships`` (wants kwargs) and
    # ``lede_top_terms`` (text-only) keeps both working.
    accepts_chunk_context: bool = True

    def __init__(self, cfg: Cfg):
        # Lazy import breaks the circular dependency with load_extractor (which
        # in turn imports CompositeExtractor via the package __init__).
        from chunkshop.extractors import load_extractor

        self._children = [load_extractor(child_cfg) for child_cfg in cfg.extractors]

    def extract(self, text: str, **kwargs) -> ExtractResult:
        tags: list[str] = []
        metadata: dict = {}
        for child in self._children:
            child_type = type(child).__name__
            try:
                # Only forward kwargs to children that opted in. The base
                # ``Extractor`` Protocol is ``extract(text) -> ExtractResult``
                # — passing kwargs to a child that doesn't accept them would
                # raise ``TypeError`` and crash the whole composite.
                if kwargs and getattr(child, "accepts_chunk_context", False):
                    r = child.extract(text, **kwargs)
                else:
                    r = child.extract(text)
            except Exception as exc:  # noqa: BLE001 — intentional, re-raised below
                raise RuntimeError(
                    f"composite extractor: child {child_type} raised: {exc}"
                ) from exc
            tags.extend(r.tags)
            metadata.update(r.metadata)
        return ExtractResult(tags=tags, metadata=metadata)

    def finalize(self, *, project_id: str = "default"):
        """Forward ``finalize()`` to children that expose one.

        Returns the FIRST non-empty edge list from a child — composite
        doesn't currently merge edges across multiple finalize-bearing
        children because there's only one in v1 (``code_relationships``).
        If your composite wraps two finalize-bearing extractors, the
        downstream consumer is expected to wire them individually.
        """
        for child in self._children:
            fn = getattr(child, "finalize", None)
            if callable(fn):
                try:
                    return fn(project_id=project_id)
                except TypeError:
                    return fn()
        return []
