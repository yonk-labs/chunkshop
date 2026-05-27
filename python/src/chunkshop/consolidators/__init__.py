"""Origin-agnostic consolidator + fact-extractor shims.

Two kinds of module live here:

1. Standalone consolidators expose ``consolidate(text: str, **kwargs) -> dict``
   returning ``{"summary": str, "facts": [ {subject,predicate,object,
   support_span,confidence}, ... ]}`` and are referenced from a user YAML via
   ``module: chunkshop.consolidators.<name>``. The default `extractive` is
   zero-network (sentence split + lightweight proposition extraction); an LLM
   consolidator is user-supplied and wired the same way.

2. Bundled fact extractors (`lede_facts`, `lede_spacy_facts`) expose
   ``extract_facts(text: str, **kwargs) -> list[dict]`` (one dict per fact, same
   field shape as above). These are NOT standalone `consolidate` callables —
   they are driven only by the bundled ``mode: lede`` / ``mode: lede_spacy``
   dispatch, which calls `extract_facts` and wraps the result.
"""
