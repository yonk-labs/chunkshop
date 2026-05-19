"""Origin-agnostic consolidator shims.

Each module exposes ``consolidate(text: str, **kwargs) -> dict`` returning
``{"summary": str, "facts": [ {subject,predicate,object,support_span,
confidence}, ... ]}`` so a user YAML references them via
``module: chunkshop.consolidators.<name>``. The default `extractive` is
zero-network (sentence split + lightweight proposition extraction); an LLM
consolidator is user-supplied and wired the same way.
"""
