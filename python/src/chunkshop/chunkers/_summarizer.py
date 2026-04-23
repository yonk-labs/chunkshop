"""Summarizer dispatch: turn a SummarizerConfig into a ``(text, doc_metadata) -> str`` callable.

Three modes (see brief SC-002):
  - external:   pull from ``doc_metadata[cfg.field]``; raises clearly if missing.
  - callable:   import ``cfg.module.cfg.function`` lazily; invoke ``fn(text, **cfg.kwargs)``.
  - passthrough: return text unchanged (baseline for A/B comparisons).

chunkshop core never imports skimr/sumy/etc. here — the callable path does the import
only when the user's YAML asks for that module.
"""
from __future__ import annotations
from importlib import import_module
from typing import Callable

from chunkshop.config import (
    CallableSummarizer,
    ExternalSummarizer,
    PassthroughSummarizer,
)


SummarizerFn = Callable[[str, dict], str]


def build_summarizer(cfg) -> SummarizerFn:
    """Return a ``(chunk_text, doc_metadata) -> summary_string`` callable.

    Raises ``RuntimeError`` on import failure, missing-field on external mode,
    or missing attribute on callable mode. All errors are actionable strings.
    """
    if isinstance(cfg, PassthroughSummarizer):
        return lambda text, meta: text

    if isinstance(cfg, ExternalSummarizer):
        field = cfg.field

        def _external(text: str, meta: dict) -> str:
            if field not in meta:
                raise RuntimeError(
                    f"external summarizer: doc.metadata has no field {field!r}. "
                    f"Available keys: {sorted(meta.keys())}"
                )
            value = meta[field]
            if not isinstance(value, str):
                raise RuntimeError(
                    f"external summarizer: doc.metadata[{field!r}] must be a string, "
                    f"got {type(value).__name__}"
                )
            return value

        return _external

    if isinstance(cfg, CallableSummarizer):
        try:
            mod = import_module(cfg.module)
        except ImportError as exc:
            raise RuntimeError(
                f"callable summarizer: could not import {cfg.module!r}: {exc}. "
                f"Install it and retry."
            ) from exc
        fn = getattr(mod, cfg.function, None)
        if fn is None:
            raise RuntimeError(
                f"callable summarizer: module {cfg.module!r} has no attribute {cfg.function!r}"
            )
        kwargs = dict(cfg.kwargs)

        def _callable(text: str, meta: dict) -> str:
            return fn(text, **kwargs)

        return _callable

    raise ValueError(f"unknown summarizer config: {type(cfg).__name__}")
