"""Consolidator dispatch: ConsolidatorConfig -> (text, meta) -> dict.

Returned dict: {"summary": str, "facts": [ {subject,predicate,object,
support_span,confidence}, ... ]}. Mirrors chunkers/_summarizer.build_summarizer:
lazy import on the callable path, actionable RuntimeError strings, chunkshop
core never imports a consolidator unless YAML asks for one.
"""
from __future__ import annotations
from importlib import import_module
from typing import Callable

from chunkshop.config import CallableConsolidator, PassthroughConsolidator

ConsolidatorFn = Callable[[str, dict], dict]


def _normalize(raw: dict) -> dict:
    facts = []
    for f in (raw.get("facts") or []):
        facts.append({
            "subject": f.get("subject"),
            "predicate": f.get("predicate"),
            "object": f.get("object"),
            "support_span": f.get("support_span") or "",
            "confidence": f.get("confidence"),
        })
    return {"summary": raw.get("summary") or "", "facts": facts}


def build_consolidator(cfg) -> ConsolidatorFn:
    if isinstance(cfg, PassthroughConsolidator):
        return lambda text, meta: {"summary": text, "facts": []}

    if isinstance(cfg, CallableConsolidator):
        try:
            mod = import_module(cfg.module)
        except ImportError as exc:
            raise RuntimeError(
                f"callable consolidator: could not import {cfg.module!r}: {exc}. "
                f"Install it and retry."
            ) from exc
        fn = getattr(mod, cfg.function, None)
        if fn is None:
            raise RuntimeError(
                f"callable consolidator: module {cfg.module!r} has no attribute "
                f"{cfg.function!r}")
        kwargs = dict(cfg.kwargs)

        def _callable(text: str, meta: dict) -> dict:
            return _normalize(fn(text, **kwargs) or {})

        return _callable

    raise ValueError(f"unknown consolidator config: {type(cfg).__name__}")
