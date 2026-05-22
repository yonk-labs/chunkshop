"""expand_hints shim — lazy passthrough to lede_spacy.expand_hints.

The ONLY chunkshop module permitted to import lede_spacy (SC-016). Wraps
lede_spacy's ImportError (synonyms without nltk/WordNet) and RuntimeError
(similar without a vector model) into an actionable chunkshop RuntimeError.
lede-spacy / expand_hints is Python-only by lede's design — there is no Rust
equivalent (the chunkshop Rust port does not expose this).
"""
from __future__ import annotations


def _load_expand():
    from lede_spacy import expand_hints as _expand
    return _expand


def expand_hints(hints, *, kinds=("lemma",), top_k=5, expand_weight=0.5):
    """Forward to lede_spacy.expand_hints; re-raise dependency errors actionably."""
    try:
        _expand = _load_expand()
    except ImportError as exc:
        raise RuntimeError(
            "hint expansion requires the [lede-spacy] extra. "
            "Install with: pip install 'lede-spacy>=0.4'."
        ) from exc
    try:
        return _expand(hints, kinds=tuple(kinds), top_k=top_k, expand_weight=expand_weight)
    except ImportError as exc:
        raise RuntimeError(
            "hint expansion kind 'synonyms' requires lede-spacy[synonyms] "
            "(nltk + WordNet). Install with: pip install 'lede-spacy[synonyms]' "
            "then: python -m nltk.downloader wordnet."
        ) from exc
    except RuntimeError as exc:
        raise RuntimeError(
            "hint expansion kind 'similar' requires a spaCy vector model "
            "(en_core_web_md or en_core_web_lg). Install with: "
            "python -m spacy download en_core_web_md."
        ) from exc


__all__ = ["expand_hints"]
