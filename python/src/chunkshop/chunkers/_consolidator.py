"""Consolidator dispatch: ConsolidatorConfig -> (text, meta) -> dict.

Returned dict: {"summary": str, "facts": [ {subject,predicate,object,
support_span,confidence}, ... ]}. Mirrors chunkers/_summarizer.build_summarizer:
lazy import on the callable path, actionable RuntimeError strings, chunkshop
core never imports a consolidator unless YAML asks for one.
"""
from __future__ import annotations
from importlib import import_module
from typing import Callable

from chunkshop.config import (
    CallableConsolidator,
    LedeConsolidator,
    LedeSpacyConsolidator,
    PassthroughConsolidator,
)
from chunkshop.chunkers._summarizer import build_summarizer

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

    if isinstance(cfg, (LedeConsolidator, LedeSpacyConsolidator)):
        if isinstance(cfg, LedeConsolidator):
            from chunkshop.consolidators.lede_facts import extract_facts
            extract_kwargs = {"max_facts": cfg.max_facts}
        else:
            from chunkshop.consolidators.lede_spacy_facts import extract_facts
            extract_kwargs = {"max_facts": cfg.max_facts, "model": cfg.model}

        summarizer_fn = build_summarizer(cfg.summarizer) if cfg.summarizer else None
        floor = cfg.confidence_floor

        def _bundled(text: str, meta: dict) -> dict:
            # Write-time floor: a null confidence coerces to 0.0 and is dropped
            # by any floor > 0 (conservative — don't embed unscored facts). NOTE
            # this diverges from fact-search's read-time --confidence-floor,
            # which KEEPS null-confidence facts. Only matters for BYO callable
            # consolidators; the bundled lede/spaCy extractors never emit null.
            facts = [f for f in extract_facts(text, **extract_kwargs)
                     if (f.get("confidence") or 0.0) >= floor]
            summary = summarizer_fn(text, meta) if summarizer_fn is not None else ""
            return _normalize({"summary": summary, "facts": facts})

        return _bundled

    raise ValueError(f"unknown consolidator config: {type(cfg).__name__}")
