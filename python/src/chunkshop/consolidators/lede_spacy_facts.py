"""lede+spaCy fact extractor — dependency-parsed SVO triples.

lede selects salient sentences; spaCy's dependency parse extracts (subject,
predicate, object). confidence is a documented heuristic: 1.0 for a full SVO
triple, 0.6 for subject+verb or verb+object; the sentence is skipped if no root
verb is found. NOT calibrated against the lede or LLM extractors.

Source: spaCy dependency labels (nsubj/nsubjpass, dobj/obj/attr/acomp, prep→pobj,
ROOT) — https://spacy.io/usage/linguistic-features#dependency-parse

Object selection captures, in priority order: (1) a direct/copular object child
of ROOT (dobj/obj/attr/acomp), then (2) a one-hop prepositional object
(ROOT → prep → pobj) — a pobj's head is the preposition, never the ROOT, so it
can't be matched as a direct child.

Gated behind the ``lede-spacy`` extra + an installed spaCy model.
"""
from __future__ import annotations

_SUBJ = {"nsubj", "nsubjpass"}
_OBJ = {"dobj", "obj", "attr", "acomp"}


def _find_object(sent, root):
    """First direct object child of root, else first one-hop prep object."""
    direct = next((t for t in sent if t.dep_ in _OBJ and t.head == root), None)
    if direct is not None:
        return direct
    return next(
        (t for t in sent
         if t.dep_ == "pobj" and t.head.dep_ == "prep" and t.head.head == root),
        None,
    )


def _salient(text: str, **kwargs) -> str:
    from chunkshop.summarizers.lede import summarize
    return summarize(text, **kwargs)


def _load_nlp(model: str):
    try:
        import spacy
    except ImportError as exc:
        raise RuntimeError(
            "lede_spacy fact extractor needs the 'lede-spacy' extra (spaCy). "
            "Install it and the model and retry."
        ) from exc
    try:
        return spacy.load(model)
    except OSError as exc:
        raise RuntimeError(
            f"spaCy model {model!r} not found. Run: python -m spacy download {model}"
        ) from exc


def extract_facts(text: str, *, max_facts: int = 20,
                  model: str = "en_core_web_sm", **kwargs) -> list[dict]:
    if not text or not text.strip():
        return []
    salient = _salient(text, **kwargs)
    if not salient.strip():
        return []
    nlp = _load_nlp(model)
    doc = nlp(salient)
    facts: list[dict] = []
    for sent in doc.sents:
        root = next((t for t in sent if t.dep_ == "ROOT" and t.pos_ in {"VERB", "AUX"}), None)
        if root is None:
            continue
        subj = next((t for t in sent if t.dep_ in _SUBJ and t.head == root), None)
        obj = _find_object(sent, root)
        if subj is None and obj is None:
            continue
        conf = 1.0 if (subj is not None and obj is not None) else 0.6
        facts.append({
            "subject": subj.text if subj is not None else None,
            "predicate": root.lemma_,
            "object": obj.text if obj is not None else None,
            "support_span": sent.text.strip(),
            "confidence": conf,
        })
        if len(facts) >= max_facts:
            break
    return facts


__all__ = ["extract_facts"]
