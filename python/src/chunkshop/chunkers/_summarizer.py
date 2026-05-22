"""Summarizer dispatch: turn a SummarizerConfig into a ``(text, doc_metadata) -> str`` callable.

Three modes (see brief SC-002):
  - external:   pull from ``doc_metadata[cfg.field]``; raises clearly if missing.
  - callable:   import ``cfg.module.cfg.function`` lazily; invoke ``fn(text, **cfg.kwargs)``.
  - passthrough: return text unchanged (baseline for A/B comparisons).

chunkshop core never imports lede/sumy/etc. here — the callable path does the import
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
from chunkshop.hints import expand_hints


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

        def _resolve(meta: dict, field, key, expected_types, type_name):
            if field is None or field not in meta:
                return None
            value = meta[field]
            if not isinstance(value, expected_types):
                raise RuntimeError(
                    f"callable summarizer: doc.metadata[{field!r}] "
                    f"(for {key}) must be {type_name}, "
                    f"got {type(value).__name__}"
                )
            return value

        def _callable(text: str, meta: dict) -> str:
            call_kwargs = dict(kwargs)
            hints = _resolve(meta, cfg.hints_from_meta, "hints",
                             (list, dict), "a list or dict")
            if hints is not None:
                call_kwargs["hints"] = hints
            focus = _resolve(meta, cfg.hint_focus_from_meta, "hint_focus",
                             (int, float), "a number")
            if isinstance(focus, bool):
                raise RuntimeError(
                    f"callable summarizer: doc.metadata[{cfg.hint_focus_from_meta!r}] "
                    f"(for hint_focus) must be a number, got bool"
                )
            if focus is not None:
                call_kwargs["hint_focus"] = float(focus)
            mode = _resolve(meta, cfg.hint_mode_from_meta, "hint_mode",
                            (str,), "a string")
            if mode is not None:
                call_kwargs["hint_mode"] = mode
            if cfg.expand is not None and call_kwargs.get("hints"):
                call_kwargs["hints"] = expand_hints(
                    call_kwargs["hints"],
                    kinds=tuple(cfg.expand.kinds),
                    top_k=cfg.expand.top_k,
                    expand_weight=cfg.expand.expand_weight,
                )
            return fn(text, **call_kwargs)

        return _callable

    raise ValueError(f"unknown summarizer config: {type(cfg).__name__}")
