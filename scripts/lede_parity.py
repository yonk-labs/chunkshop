#!/usr/bin/env python
"""Python-side parity harness for the lede-backed enrichment features.

Runs the same fixed input through the Python chunkshop extractors +
consolidator and prints one JSON object per feature, to diff against the Rust
example (`rust/chunkshop/examples/lede_parity.rs`).

Run with the chunkshop venv (lede>=0.5 installed):
    .venv/bin/python scripts/lede_parity.py        # from python/, or
    python scripts/lede_parity.py                  # if chunkshop is importable
"""
from __future__ import annotations

import json

TEXT = (
    "Acme Corp raised $5 million in 2023. The company hired 40 engineers "
    "and opened a Berlin office on 2024-01-15. Revenue increased 300 percent. "
    "CEO Bob Smith said growth would continue."
)


import sys

# Optional argv override for ad-hoc probing; defaults to TEXT.
if len(sys.argv) > 1:
    TEXT = sys.argv[1]


def emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True))


def run_extractor(feature: str, cfg) -> None:
    from chunkshop.extractors import load_extractor

    try:
        ex = load_extractor(cfg)
        r = ex.extract(TEXT)
        emit({"impl": "python", "feature": feature, "tags": list(r.tags), "metadata": r.metadata})
    except Exception as e:  # spaCy model absent, etc. — report, don't crash the run
        emit({"impl": "python", "feature": feature, "error": f"{type(e).__name__}: {e}"})


def main() -> None:
    from chunkshop.config import (
        LedeReportExtractor,
        LedeTopTermsExtractor,
        SpacyEntitiesExtractor,
    )

    run_extractor("lede_top_terms", LedeTopTermsExtractor(type="lede_top_terms", n=8))
    run_extractor("lede_report", LedeReportExtractor(type="lede_report", max_facts=10))
    # Python's nearest counterpart to Rust `lede_entities` is `spacy_entities`
    # (different engine — spaCy NER vs lede-enrich gazetteer).
    run_extractor("spacy_entities", SpacyEntitiesExtractor(type="spacy_entities"))

    # consolidator: mode lede -> lede_facts.extract_facts
    try:
        from chunkshop.consolidators.lede_facts import extract_facts

        facts = extract_facts(TEXT, max_facts=10)
        emit({"impl": "python", "feature": "consolidator_lede", "facts": facts})
    except Exception as e:
        emit({"impl": "python", "feature": "consolidator_lede", "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
