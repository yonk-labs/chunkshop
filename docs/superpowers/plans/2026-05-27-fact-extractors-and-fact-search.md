# Bundled Fact Extractors, Caveman Reducer & fact-search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship batteries-included fact extraction (`lede`, `lede+spaCy`) and a `caveman` text reducer for chunkshop's agent-memory path, plus a `fact-search` CLI that returns facts with their chunk/doc/summary breadcrumb.

**Architecture:** Two orthogonal axes. *Fact extraction* fills the consolidator's fact slot via new first-class `ConsolidatorConfig` variants (hybrid: keep `CallableConsolidator` escape hatch). *Reduction* (`caveman`) is a second implementation of the existing summarizer contract `(text, **kwargs) -> str`. Facts stay co-located as `kind='fact'` rows; `fact-search` reconstructs the fact→chunk→doc graph by query-time join. A `metadata_not` WHERE predicate keeps facts out of normal search by default.

**Tech Stack:** Python 3.12/3.13, pydantic v2 (discriminated unions, `extra="forbid"`), Click CLI, psycopg + pgvector, lede / lede-spacy (spaCy) behind pip extras, pytest.

**Spec:** `docs/superpowers/specs/2026-05-27-chunkshop-fact-extractors-and-fact-search-design.md` · **Issue:** yonk-labs/chunkshop#30

**Run all commands from `python/`.** Tests assume `uv run --no-sync pytest`. The Postgres DSN for integration tests is `$CHUNKSHOP_TEST_DSN` (tests skip if unset/unreachable).

---

## File Structure

**Phase A — caveman reducer (no new deps, no DB)**
- Create `src/chunkshop/summarizers/caveman.py` — `summarize(text, **kwargs) -> str`, deterministic fluff/stopword reduction.
- Modify `src/chunkshop/search_common.py` — add optional `compress_fn` to `summarize_hits` + `search`.
- Modify `src/chunkshop/cli.py` — add `--compress` flag to the `search` command.
- Tests: `tests/chunkshop/test_caveman_reducer.py`, `tests/chunkshop/test_summarize_hits_compress.py`.

**Phase B — bundled fact extractors (lede / lede+spaCy)**
- Create `src/chunkshop/consolidators/__init__.py`, `src/chunkshop/consolidators/lede_facts.py`, `src/chunkshop/consolidators/lede_spacy_facts.py`.
- Modify `src/chunkshop/config.py` — add `LedeConsolidator`, `LedeSpacyConsolidator` to the `ConsolidatorConfig` union.
- Modify `src/chunkshop/chunkers/_consolidator.py` — dispatch the new modes, compose fact-extractor + optional summarizer slot + `confidence_floor`.
- Tests: `tests/chunkshop/test_lede_facts.py`, `tests/chunkshop/test_lede_spacy_facts.py`, `tests/chunkshop/test_consolidator_dispatch.py`.

**Phase C — fact-search + kind-aware filtering (DB integration)**
- Modify `src/chunkshop/search.py` — add `metadata_not` predicate to `_build_where`.
- Modify `src/chunkshop/cli.py` — default-exclude facts in `search` (+ `--include-facts`), add the `fact-search` command + a `_fetch_chunk` breadcrumb helper.
- Tests: `tests/chunkshop/test_build_where_metadata_not.py`, `tests/chunkshop/test_fact_search.py`.

Each phase ends compilable and tested; commit at the end of every task.

---

## PHASE A — Caveman Reducer

### Task A1: caveman reducer module

**Files:**
- Create: `src/chunkshop/summarizers/caveman.py`
- Test: `tests/chunkshop/test_caveman_reducer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_caveman_reducer.py
from chunkshop.summarizers.caveman import summarize


def test_strips_stopwords_keeps_content_words():
    out = summarize("the cat is on the mat and it is happy")
    # stopwords (the, is, on, and, it) removed; content words kept in order
    assert out == "cat mat happy"


def test_empty_and_whitespace_return_empty():
    assert summarize("") == ""
    assert summarize("   \n  ") == ""


def test_idempotent():
    once = summarize("the quick brown fox jumps over the lazy dog")
    twice = summarize(once)
    assert once == twice


def test_preserves_token_order_and_case():
    out = summarize("The Eiffel Tower is in Paris")
    assert out == "Eiffel Tower Paris"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_caveman_reducer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunkshop.summarizers.caveman'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/chunkshop/summarizers/caveman.py
"""caveman reducer — strip fluff/stopwords so an LLM sees fewer tokens.

Implements the summarizer contract ``summarize(text, **kwargs) -> str`` so it is
swappable anywhere lede is (CallableSummarizer ``module:`` path, the read-time
``compress_fn`` in ``summarize_hits``). This is a REDUCTION strategy, NOT a fact
extractor: it shrinks any text (facts, chunks, docs, summaries) by dropping
low-information tokens while keeping meaning-bearing words. Pure Python, no deps,
deterministic, and idempotent (running it twice changes nothing).
"""
from __future__ import annotations

# Minimal high-frequency English function-word list. Deliberately small and
# conservative — we only drop words that carry ~no retrieval/answer signal.
_STOPWORDS = frozenset(
    """a an and are as at be been being but by for from had has have he her him
    his i in into is it its of on or our she that the their them they this to
    was we were will with you your over it's""".split()
)


def summarize(text: str, **kwargs) -> str:
    """Return *text* with stopwords removed, token order and case preserved.

    Extra kwargs (e.g. ``max_length``) are accepted and ignored so caveman
    satisfies the same ``(text, **kwargs) -> str`` contract as lede and can be
    dropped into any summarizer slot without signature surprises.
    """
    if not text or not text.strip():
        return ""
    kept = [
        tok
        for tok in text.split()
        if tok.strip(".,;:!?\"'()[]").casefold() not in _STOPWORDS
    ]
    return " ".join(kept)


__all__ = ["summarize"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/chunkshop/test_caveman_reducer.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/chunkshop/summarizers/caveman.py tests/chunkshop/test_caveman_reducer.py
git commit -m "feat(summarizers): add caveman fluff-reduction reducer"
```

---

### Task A2: optional read-time compression in summarize_hits + search

**Files:**
- Modify: `src/chunkshop/search_common.py` (`summarize_hits` ~line 165, `search` ~line 317, `_summarize_for_query` ~line 278)
- Test: `tests/chunkshop/test_summarize_hits_compress.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_summarize_hits_compress.py
from chunkshop.search_common import Hit, summarize_hits


def _fake_summarize(text, **kwargs):
    # identity summarizer: returns the body unchanged so we can observe compress
    return text


def _hit(text):
    return Hit(doc_id="d", seq_num=0, text=text, score=1.0, metadata={}, legs=("fts",))


def test_compress_fn_applied_to_summary():
    hits = [_hit("the cat is on the mat")]
    out = summarize_hits(
        hits, _fake_summarize, prepend_headings=False,
        compress_fn=lambda s: s.replace("the ", ""),
    )
    assert "the " not in out
    assert "cat" in out


def test_compress_fn_default_off():
    hits = [_hit("the cat is on the mat")]
    out = summarize_hits(hits, _fake_summarize, prepend_headings=False)
    assert out == "the cat is on the mat"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_summarize_hits_compress.py -v`
Expected: FAIL — `TypeError: summarize_hits() got an unexpected keyword argument 'compress_fn'`

- [ ] **Step 3: Add `compress_fn` to `summarize_hits`**

In `src/chunkshop/search_common.py`, change the `summarize_hits` signature to add the parameter (keep all existing params):

```python
def summarize_hits(
    hits: list[Hit],
    summarize_fn: Callable[..., str],
    *,
    max_length: int = 1200,
    hints: Sequence[str] | Mapping[str, float] | None = None,
    hint_focus: float = 0.7,
    hint_mode: str = "soft",
    prepend_headings: bool = True,
    use_embedded: bool = True,
    compress_fn: Optional[Callable[[str], str]] = None,
) -> str:
```

Then, immediately after the line `summary = summarize_fn(body, **call_kwargs)`, insert:

```python
    # Optional read-time reduction (e.g. caveman). Applied to the produced
    # summary BEFORE headings are prepended, so structural headings survive.
    if compress_fn is not None:
        summary = compress_fn(summary)
```

- [ ] **Step 4: Thread `compress_fn` through `search` and `_summarize_for_query`**

In `_summarize_for_query` (~line 278) add `compress_fn=None` to the signature and forward it to `summarize_hits`:

```python
def _summarize_for_query(
    hits: list[Hit],
    query: str,
    *,
    summarize_fn: Optional[Callable[..., str]],
    summary_hints: Optional[list[str]],
    summary_expand,
    max_length: int,
    compress_fn: Optional[Callable[[str], str]] = None,
) -> str:
    ...
    return summarize_hits(
        hits, summarize_fn, max_length=max_length, hints=hints, compress_fn=compress_fn
    )
```

In `search` (~line 317) add `compress_fn=None` to the signature and forward it in the `_summarize_for_query(...)` call:

```python
    summary = _summarize_for_query(
        hits,
        query or "",
        summarize_fn=summarize_fn,
        summary_hints=summary_hints,
        summary_expand=summary_expand,
        max_length=summary_max_length,
        compress_fn=compress_fn,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/chunkshop/test_summarize_hits_compress.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the broader search suite to confirm no regressions**

Run: `uv run --no-sync pytest tests/chunkshop/ -k "search_common or summarize" -q`
Expected: PASS (existing tests unaffected — `compress_fn` defaults to None)

- [ ] **Step 7: Commit**

```bash
git add src/chunkshop/search_common.py tests/chunkshop/test_summarize_hits_compress.py
git commit -m "feat(search): optional read-time compress_fn in summarize_hits"
```

---

### Task A3: wire `--compress` into the search CLI

**Files:**
- Modify: `src/chunkshop/cli.py` (`search` command ~line 630-810)
- Test: `tests/chunkshop/test_caveman_reducer.py` (add a CLI smoke via import — no DB)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/chunkshop/test_caveman_reducer.py
def test_search_command_exposes_compress_flag():
    from chunkshop.cli import search as search_cmd
    names = {p.name for p in search_cmd.params}
    assert "compress" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_caveman_reducer.py::test_search_command_exposes_compress_flag -v`
Expected: FAIL — `AssertionError` (no `compress` param)

- [ ] **Step 3: Add the flag and wire caveman**

In `src/chunkshop/cli.py`, add a Click option just above `def search(...)` (next to `--json`):

```python
@click.option("--compress", "compress", is_flag=True, default=False,
              help="Strip fluff words from the summary via the caveman reducer "
                   "(only affects --return summary/summary+chunks).")
```

Add `compress` to the `search(...)` parameter list. Inside `search`, where `summarize_fn` is resolved (currently `from chunkshop.summarizers.lede import summarize as summarize_fn`), add below it:

```python
        compress_fn = None
        if compress and return_mode != "chunks":
            from chunkshop.summarizers.caveman import summarize as compress_fn
```

Then pass `compress_fn=compress_fn` into the `_search(...)` call (the `chunkshop.search_common.search` invocation in the command body).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/chunkshop/test_caveman_reducer.py -v`
Expected: PASS (all caveman tests pass)

- [ ] **Step 5: Confirm the CLI still imports/builds**

Run: `uv run --no-sync chunkshop search --help`
Expected: help text lists `--compress`

- [ ] **Step 6: Commit**

```bash
git add src/chunkshop/cli.py tests/chunkshop/test_caveman_reducer.py
git commit -m "feat(cli): add --compress (caveman) to search"
```

**End of Phase A — caveman reducer is shippable on its own.**

---

## PHASE B — Bundled Fact Extractors

### Task B1: lede fact extractor

**Files:**
- Create: `src/chunkshop/consolidators/__init__.py` (empty package marker with docstring)
- Create: `src/chunkshop/consolidators/lede_facts.py`
- Test: `tests/chunkshop/test_lede_facts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_lede_facts.py
from chunkshop.consolidators import lede_facts


def test_empty_text_yields_no_facts():
    assert lede_facts.extract_facts("") == []


def test_facts_have_contract_shape_and_decaying_confidence(monkeypatch):
    # Stub the lede summarizer shim so the test is deterministic and dep-free.
    monkeypatch.setattr(
        lede_facts, "_lede_summary",
        lambda text, **kw: "Alpha is first. Beta is second. Gamma is third.",
    )
    facts = lede_facts.extract_facts("ignored", max_facts=2)
    assert len(facts) == 2
    for f in facts:
        assert set(f) == {"subject", "predicate", "object", "support_span", "confidence"}
    # rank-decay: first fact more confident than the second
    assert facts[0]["confidence"] > facts[1]["confidence"]
    assert facts[0]["support_span"] == "Alpha is first."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_lede_facts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunkshop.consolidators'`

- [ ] **Step 3: Write the package marker + implementation**

```python
# src/chunkshop/consolidators/__init__.py
"""Bundled fact extractors (Axis 1) for the ConsolidationChunker fact slot.

Each module exposes ``extract_facts(text, **kwargs) -> list[dict]`` where every
dict is ``{subject, predicate, object, support_span, confidence}``. Heavyweight
backends (lede, spaCy) are imported lazily and gated behind pip extras, so
chunkshop core never loads them unless a cell's YAML selects that consolidator.
"""
```

```python
# src/chunkshop/consolidators/lede_facts.py
"""lede fact extractor — salient sentences as propositions.

lede selects the most salient sentences; each becomes one fact whose
``support_span`` is the sentence and whose ``subject/predicate/object`` are left
None (sparse/proposition-style, matching SP-A's extractive degrade path).
``confidence`` is a rank-decay score in [0,1] (first sentence most confident).
This is the documented per-extractor meaning of confidence for lede — it is NOT
calibrated against the spaCy or LLM extractors.

Gated behind the ``lede`` extra; raises an actionable error if lede is absent.
"""
from __future__ import annotations
import re

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _lede_summary(text: str, **kwargs) -> str:
    """Indirection point so tests can stub lede without the extra installed."""
    from chunkshop.summarizers.lede import summarize
    return summarize(text, **kwargs)


def extract_facts(text: str, *, max_facts: int = 10, **kwargs) -> list[dict]:
    if not text or not text.strip():
        return []
    summary = _lede_summary(text, **kwargs)
    sentences = [s.strip() for s in _SENT_SPLIT.split(summary) if s.strip()]
    sentences = sentences[:max_facts]
    n = len(sentences)
    facts: list[dict] = []
    for i, sent in enumerate(sentences):
        facts.append({
            "subject": None,
            "predicate": None,
            "object": None,
            "support_span": sent,
            "confidence": round(1.0 - (i / n if n else 0.0), 3),
        })
    return facts


__all__ = ["extract_facts"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/chunkshop/test_lede_facts.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/chunkshop/consolidators/__init__.py src/chunkshop/consolidators/lede_facts.py tests/chunkshop/test_lede_facts.py
git commit -m "feat(consolidators): lede salient-sentence fact extractor"
```

---

### Task B2: lede+spaCy fact extractor (SVO triples)

**Files:**
- Create: `src/chunkshop/consolidators/lede_spacy_facts.py`
- Test: `tests/chunkshop/test_lede_spacy_facts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_lede_spacy_facts.py
import pytest

from chunkshop.consolidators import lede_spacy_facts


def test_empty_text_yields_no_facts():
    assert lede_spacy_facts.extract_facts("") == []


def test_svo_triple_from_simple_sentence(monkeypatch):
    # Avoid depending on lede: feed the salient text straight through.
    monkeypatch.setattr(lede_spacy_facts, "_salient", lambda text, **kw: text)
    spacy = pytest.importorskip("spacy")
    try:
        spacy.load("en_core_web_sm")
    except OSError:
        pytest.skip("en_core_web_sm model not installed")

    facts = lede_spacy_facts.extract_facts("Alice wrote the report.")
    assert any(
        f["subject"] == "Alice" and f["predicate"] == "wrote" and "report" in (f["object"] or "")
        for f in facts
    )
    for f in facts:
        assert 0.0 <= f["confidence"] <= 1.0
        assert f["support_span"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_lede_spacy_facts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunkshop.consolidators.lede_spacy_facts'`

- [ ] **Step 3: Write the implementation**

```python
# src/chunkshop/consolidators/lede_spacy_facts.py
"""lede+spaCy fact extractor — dependency-parsed SVO triples.

lede selects salient sentences; spaCy's dependency parse extracts (subject,
predicate, object) from each. confidence is a documented heuristic: 1.0 for a
full SVO triple, 0.6 for subject+verb or verb+object, and the sentence is
skipped if no root verb is found. confidence is NOT calibrated against the lede
or LLM extractors.

Source: spaCy dependency labels (``nsubj``/``nsubjpass``, ``dobj``/``pobj``,
``ROOT``) — https://spacy.io/usage/linguistic-features#dependency-parse

Gated behind the ``lede-spacy`` extra + an installed spaCy model.
"""
from __future__ import annotations

_SUBJ = {"nsubj", "nsubjpass"}
_OBJ = {"dobj", "obj", "pobj", "attr"}


def _salient(text: str, **kwargs) -> str:
    """Indirection point so tests can stub the lede pre-filter."""
    from chunkshop.summarizers.lede import summarize
    return summarize(text, **kwargs)


def _load_nlp(model: str):
    try:
        import spacy
    except ImportError as exc:  # pragma: no cover - exercised via extra gating
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
        obj = next((t for t in sent if t.dep_ in _OBJ and t.head == root), None)
        if subj is None and obj is None:
            continue
        if subj is not None and obj is not None:
            conf = 1.0
        else:
            conf = 0.6
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
```

- [ ] **Step 4: Run test to verify it passes (or skips cleanly)**

Run: `uv run --no-sync pytest tests/chunkshop/test_lede_spacy_facts.py -v`
Expected: PASS if spaCy + model present; otherwise the SVO test SKIPS and the empty-text test PASSES. Neither errors.

- [ ] **Step 5: Commit**

```bash
git add src/chunkshop/consolidators/lede_spacy_facts.py tests/chunkshop/test_lede_spacy_facts.py
git commit -m "feat(consolidators): lede+spaCy SVO-triple fact extractor"
```

---

### Task B3: first-class consolidator configs (hybrid)

**Files:**
- Modify: `src/chunkshop/config.py` (Consolidator section ~line 425-448, and the `ConsolidationChunker.model_rebuild()` call ~line 647)
- Test: `tests/chunkshop/test_consolidator_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_consolidator_dispatch.py
from chunkshop.config import ConsolidationChunker


def test_lede_consolidator_parses():
    cfg = ConsolidationChunker.model_validate({
        "type": "consolidation",
        "base": {"type": "sentence_aware", "max_sentences": 5},
        "consolidator": {"mode": "lede", "confidence_floor": 0.3, "max_facts": 8},
    })
    assert cfg.consolidator.mode == "lede"
    assert cfg.consolidator.confidence_floor == 0.3


def test_lede_spacy_consolidator_parses_with_summarizer_slot():
    cfg = ConsolidationChunker.model_validate({
        "type": "consolidation",
        "base": {"type": "sentence_aware", "max_sentences": 5},
        "consolidator": {
            "mode": "lede_spacy",
            "summarizer": {"mode": "callable", "module": "chunkshop.summarizers.caveman"},
        },
    })
    assert cfg.consolidator.mode == "lede_spacy"
    assert cfg.consolidator.summarizer.module == "chunkshop.summarizers.caveman"


def test_confidence_floor_bounds_rejected():
    import pytest
    with pytest.raises(Exception):
        ConsolidationChunker.model_validate({
            "type": "consolidation",
            "base": {"type": "sentence_aware", "max_sentences": 5},
            "consolidator": {"mode": "lede", "confidence_floor": 1.5},
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_consolidator_dispatch.py -v`
Expected: FAIL — pydantic rejects `mode: 'lede'` (not in the current union discriminator)

- [ ] **Step 3: Add the two config classes and extend the union**

In `src/chunkshop/config.py`, immediately after `class PassthroughConsolidator(_Base):` (before `ConsolidatorConfig = Annotated[...]`), add:

```python
class LedeConsolidator(_Base):
    """Bundled: lede salient-sentence fact extractor + optional summarizer slot.

    summary is filled by the summarizer slot (lede/caveman/external) when set,
    else left empty (the chunker falls back to the episode text). Facts below
    confidence_floor are dropped before embedding (storage lever)."""
    mode: Literal["lede"]
    summarizer: Optional[SummarizerConfig] = None
    confidence_floor: float = Field(default=0.0, ge=0.0, le=1.0)
    max_facts: int = Field(default=10, ge=1)


class LedeSpacyConsolidator(_Base):
    """Bundled: lede+spaCy dependency-parsed SVO triples + optional summarizer."""
    mode: Literal["lede_spacy"]
    summarizer: Optional[SummarizerConfig] = None
    confidence_floor: float = Field(default=0.0, ge=0.0, le=1.0)
    max_facts: int = Field(default=20, ge=1)
    model: str = "en_core_web_sm"
```

Replace the `ConsolidatorConfig` union with:

```python
ConsolidatorConfig = Annotated[
    Union[
        CallableConsolidator,
        PassthroughConsolidator,
        LedeConsolidator,
        LedeSpacyConsolidator,
    ],
    Field(discriminator="mode"),
]
```

`SummarizerConfig`, `Optional`, `Field`, `Literal`, `Union` are already imported/defined above this section in `config.py` — no new imports needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/chunkshop/test_consolidator_dispatch.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Confirm full config still validates (no union regressions)**

Run: `uv run --no-sync pytest tests/chunkshop/ -k "config" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/chunkshop/config.py tests/chunkshop/test_consolidator_dispatch.py
git commit -m "feat(config): first-class lede/lede_spacy consolidator variants"
```

---

### Task B4: build_consolidator dispatch + summarizer slot + confidence_floor

**Files:**
- Modify: `src/chunkshop/chunkers/_consolidator.py`
- Test: `tests/chunkshop/test_consolidator_dispatch.py` (add behavior tests)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/chunkshop/test_consolidator_dispatch.py
from chunkshop.config import LedeConsolidator
from chunkshop.chunkers._consolidator import build_consolidator


def test_build_lede_applies_confidence_floor(monkeypatch):
    # Stub the extractor so the test is deterministic + dep-free.
    import chunkshop.consolidators.lede_facts as lf
    monkeypatch.setattr(lf, "extract_facts", lambda text, **kw: [
        {"subject": None, "predicate": None, "object": None,
         "support_span": "high", "confidence": 0.9},
        {"subject": None, "predicate": None, "object": None,
         "support_span": "low", "confidence": 0.1},
    ])
    fn = build_consolidator(LedeConsolidator(mode="lede", confidence_floor=0.5))
    out = fn("some episode text", {})
    spans = [f["support_span"] for f in out["facts"]]
    assert spans == ["high"]          # 0.1 fact dropped by the floor
    assert out["summary"] == ""        # no summarizer slot -> empty summary


def test_build_lede_summarizer_slot_fills_summary(monkeypatch):
    import chunkshop.consolidators.lede_facts as lf
    monkeypatch.setattr(lf, "extract_facts", lambda text, **kw: [])
    cfg = LedeConsolidator.model_validate({
        "mode": "lede",
        "summarizer": {"mode": "passthrough"},
    })
    fn = build_consolidator(cfg)
    out = fn("EPISODE BODY", {})
    assert out["summary"] == "EPISODE BODY"   # passthrough summarizer returns text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_consolidator_dispatch.py -k build -v`
Expected: FAIL — `build_consolidator` raises `ValueError: unknown consolidator config: LedeConsolidator`

- [ ] **Step 3: Extend build_consolidator**

In `src/chunkshop/chunkers/_consolidator.py`, update the imports:

```python
from chunkshop.config import (
    CallableConsolidator,
    PassthroughConsolidator,
    LedeConsolidator,
    LedeSpacyConsolidator,
)
from chunkshop.chunkers._summarizer import build_summarizer
```

Add a helper and two dispatch branches inside `build_consolidator`, before the final `raise ValueError`:

```python
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
            facts = [
                f for f in extract_facts(text, **extract_kwargs)
                if (f.get("confidence") or 0.0) >= floor
            ]
            summary = summarizer_fn(text, meta) if summarizer_fn is not None else ""
            return _normalize({"summary": summary, "facts": facts})

        return _bundled
```

Note: `build_summarizer` returns a `(text, meta) -> str` callable, so the summary slot composes cleanly. `_normalize` (already in this file) guarantees the contract shape.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/chunkshop/test_consolidator_dispatch.py -k build -v`
Expected: PASS (2 passed)

- [ ] **Step 5: End-to-end chunker sanity (no DB) — facts flow through ConsolidationChunker**

```python
# append to tests/chunkshop/test_consolidator_dispatch.py
from chunkshop.chunkers import load_chunker
from chunkshop.sources.base import Document


def test_consolidation_chunker_emits_fact_chunks(monkeypatch):
    import chunkshop.consolidators.lede_facts as lf
    monkeypatch.setattr(lf, "extract_facts", lambda text, **kw: [
        {"subject": "A", "predicate": "is", "object": "B",
         "support_span": "A is B", "confidence": 0.9},
    ])
    chunker = load_chunker({
        "type": "consolidation",
        "base": {"type": "sentence_aware", "max_sentences": 5},
        "consolidator": {"mode": "lede"},
    })
    chunks = chunker.chunk(Document(id="doc1", content="A is B. C is D.", metadata={}))
    kinds = [c.metadata.get("kind") for c in chunks]
    assert "episode" in kinds and "fact" in kinds
```

Run: `uv run --no-sync pytest tests/chunkshop/test_consolidator_dispatch.py -v`
Expected: PASS (all). If `load_chunker`'s import path differs, confirm with `grep -n "def load_chunker" src/chunkshop/chunkers/__init__.py` and adjust the import.

- [ ] **Step 6: Commit**

```bash
git add src/chunkshop/chunkers/_consolidator.py tests/chunkshop/test_consolidator_dispatch.py
git commit -m "feat(consolidators): dispatch lede/lede_spacy + summarizer slot + confidence_floor"
```

**End of Phase B — bundled fact extractors are shippable.**

---

## PHASE C — fact-search + kind-aware filtering

### Task C1: `metadata_not` WHERE predicate

**Files:**
- Modify: `src/chunkshop/search.py` (`_build_where` ~line 115-200)
- Test: `tests/chunkshop/test_build_where_metadata_not.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_build_where_metadata_not.py
from chunkshop.search import _build_where


def test_metadata_not_emits_is_distinct_from():
    sql, params = _build_where({"metadata_not": {"kind": "fact"}})
    # IS DISTINCT FROM keeps rows whose metadata has NO 'kind' key (NULL),
    # so the filter is a no-op for non-memory chunk rows.
    assert "IS DISTINCT FROM" in sql
    assert "fact" in params


def test_metadata_not_combines_with_equality():
    sql, params = _build_where({"metadata": {"source": "x"}, "metadata_not": {"kind": "fact"}})
    assert sql.count("metadata") >= 2
    assert "x" in params and "fact" in params


def test_unknown_key_still_rejected():
    import pytest
    with pytest.raises(ValueError):
        _build_where({"bogus": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_build_where_metadata_not.py -v`
Expected: FAIL — `metadata_not` rejected as an unsupported where key.

- [ ] **Step 3: Add `metadata_not` to `_build_where`**

In `src/chunkshop/search.py`, find the allowlist line in `_build_where`:

```python
    unknown = set(where) - {"tags", "source", "metadata", "column_in", "column_like"}
```

Add `"metadata_not"`:

```python
    unknown = set(where) - {"tags", "source", "metadata", "metadata_not", "column_in", "column_like"}
```

Then, after the existing `meta = where.get("metadata")` block (the one that appends `metadata ->> key = %s` clauses), add a parallel block. Match the existing block's clause style; the typical pattern is appending to a `clauses` list and `params` list:

```python
    meta_not = where.get("metadata_not")
    if meta_not is not None:
        if not isinstance(meta_not, dict):
            raise ValueError("where['metadata_not'] must be a dict")
        for key, value in meta_not.items():
            # IS DISTINCT FROM keeps rows missing the key (NULL) -> no-op for
            # non-memory chunks; only rows whose metadata[key] == value are cut.
            clauses.append(f"(metadata ->> %s) IS DISTINCT FROM %s")
            params.extend([key, value])
```

Confirm the surrounding variable names (`clauses`, `params`) by reading the existing `metadata` block in the same function and match them exactly. The metadata key name is spliced as a bound parameter (`%s`), not formatted into SQL — keep it that way for injection safety.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/chunkshop/test_build_where_metadata_not.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/chunkshop/search.py tests/chunkshop/test_build_where_metadata_not.py
git commit -m "feat(search): metadata_not WHERE predicate (IS DISTINCT FROM)"
```

---

### Task C2: default-exclude facts in `search` (+ `--include-facts`)

**Files:**
- Modify: `src/chunkshop/cli.py` (`search` command)
- Test: `tests/chunkshop/test_fact_search.py` (param-shape test, no DB)

- [ ] **Step 1: Write the failing test**

```python
# tests/chunkshop/test_fact_search.py
def test_search_has_include_facts_flag():
    from chunkshop.cli import search as search_cmd
    names = {p.name for p in search_cmd.params}
    assert "include_facts" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/chunkshop/test_fact_search.py::test_search_has_include_facts_flag -v`
Expected: FAIL — no `include_facts` param.

- [ ] **Step 3: Add the flag + default exclusion**

In `src/chunkshop/cli.py`, add an option above `def search(...)`:

```python
@click.option("--include-facts", "include_facts", is_flag=True, default=False,
              help="Include kind='fact' rows in results (excluded by default).")
```

Add `include_facts` to the `search(...)` parameter list. After `parsed_where = _parse_where(where_opts) or {}`, add:

```python
        if not include_facts and "metadata_not" not in parsed_where:
            parsed_where["metadata_not"] = {"kind": "fact"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/chunkshop/test_fact_search.py::test_search_has_include_facts_flag -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/chunkshop/cli.py tests/chunkshop/test_fact_search.py
git commit -m "feat(cli): search excludes facts by default (--include-facts to opt in)"
```

---

### Task C3: `fact-search` command + breadcrumb enrichment

**Files:**
- Modify: `src/chunkshop/cli.py` (add `_fetch_chunk` helper + `fact_search` command)
- Test: `tests/chunkshop/test_fact_search.py` (param-shape unit + Postgres integration)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/chunkshop/test_fact_search.py
import os
import pytest


def test_fact_search_command_registered():
    from chunkshop.cli import cli
    assert "fact-search" in cli.commands


DSN = os.environ.get("CHUNKSHOP_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="CHUNKSHOP_TEST_DSN not set")
def test_fact_search_returns_breadcrumb(tmp_path):
    # Build a tiny memory cell, ingest one doc that yields an episode + fact,
    # then assert fact-search returns the fact WITH its chunk + doc breadcrumb.
    # (Full fixture wiring mirrors tests/chunkshop/test_end_to_end_samples_corpus.py;
    # reuse its cell-config + runner helpers.)
    from click.testing import CliRunner
    from chunkshop.cli import cli

    cfg_path = _write_memory_cell_yaml(tmp_path, DSN)   # helper below
    _ingest_one_fact_doc(cfg_path)                       # helper below

    res = CliRunner().invoke(cli, [
        "fact-search", "--config", str(cfg_path),
        "--query", "alpha", "--json",
    ])
    assert res.exit_code == 0, res.output
    import json
    payload = json.loads(res.output)
    assert payload, "expected at least one fact hit"
    first = payload[0]
    assert {"fact", "chunk", "doc_id"} <= set(first)
    assert first["chunk"]["doc_id"] == first["doc_id"]
```

> The two helpers (`_write_memory_cell_yaml`, `_ingest_one_fact_doc`) should be
> written to mirror the existing memory-cell fixtures. Read
> `tests/chunkshop/` for the closest MemorySink/ConsolidationChunker test and
> copy its setup (consolidator `mode: lede` with a stubbed/real extractor that
> yields at least one fact containing "alpha"). Keep the doc tiny.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest tests/chunkshop/test_fact_search.py -v`
Expected: FAIL — `'fact-search' not in cli.commands` (integration test skips without DSN).

- [ ] **Step 3: Add the `_fetch_chunk` helper**

In `src/chunkshop/cli.py`, add near the other private helpers (e.g. below `_enrich_with_chunk_metadata`):

```python
def _fetch_chunk(dsn: str, schema: str, table: str, doc_id: str, seq_num: int) -> Optional[dict]:
    """Fetch one row's (doc_id, seq_num, original_content, metadata) for breadcrumb
    reconstruction. Returns None if absent. Uses the same psycopg path as the
    rest of the CLI read helpers."""
    import psycopg
    fq = f'"{schema}"."{table}"'
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT doc_id, seq_num, original_content, metadata FROM {fq} "
            f"WHERE doc_id = %s AND seq_num = %s",
            (doc_id, seq_num),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"doc_id": row[0], "seq_num": row[1], "text": row[2], "metadata": row[3]}
```

Confirm the psycopg connect/identifier-quoting pattern matches `_enrich_with_chunk_metadata` / `_impact_query_one_direction` already in `cli.py`; reuse their exact connection helper if one exists rather than introducing a new `psycopg.connect`.

- [ ] **Step 4: Add the `fact-search` command**

```python
@cli.command("fact-search")
@click.option("--config", required=True, type=click.Path(exists=True, path_type=Path),
              help="Path to the YAML/JSON cell config.")
@click.option("--query", required=True, help="Free-text query string.")
@click.option("--k", default=10, type=int, show_default=True, help="Max facts to return.")
@click.option("--confidence-floor", "confidence_floor", default=0.0, type=float,
              show_default=True, help="Drop facts whose confidence is below this.")
@click.option("--summary/--no-summary", "want_summary", default=False,
              help="Attach a lede summary of each fact's source chunk.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def fact_search(config, query, k, confidence_floor, want_summary, as_json):
    """Search a cell's facts and return each with its chunk/doc breadcrumb."""
    import json as _json
    import yaml as _yaml
    from chunkshop.config import CellConfig
    from chunkshop.embedders import load_embedder
    from chunkshop.search_common import search as _search

    try:
        cfg = CellConfig.model_validate(_yaml.safe_load(Path(config).read_text()))
        emb = load_embedder(cfg.embedder)
        qv = emb.embed([query])[0]
        tgt = cfg.target
        result = _search(
            cfg.target.dsn, schema=tgt.schema, table=tgt.table,
            query=query, query_vec=qv, k=k,
            where={"metadata": {"kind": "fact"}}, return_mode="chunks",
        )
        summarize_fn = None
        if want_summary:
            from chunkshop.summarizers.lede import summarize as summarize_fn

        out = []
        for hit in result.chunks:
            conf = hit.metadata.get("confidence")
            if conf is not None and conf < confidence_floor:
                continue
            parent_seq = hit.metadata.get("source_chunk_seq")
            chunk = None
            if parent_seq is not None:
                chunk = _fetch_chunk(cfg.target.dsn, tgt.schema, tgt.table, hit.doc_id, parent_seq)
            entry = {
                "fact": {
                    "subject": hit.metadata.get("subject"),
                    "predicate": hit.metadata.get("predicate"),
                    "object": hit.metadata.get("object"),
                    "support_span": hit.text,
                    "confidence": conf,
                },
                "doc_id": hit.doc_id,
                "chunk": chunk,
                "score": hit.score,
            }
            if want_summary and chunk and summarize_fn is not None:
                entry["summary"] = summarize_fn(chunk["text"], max_length=300)
            out.append(entry)

        if as_json:
            click.echo(_json.dumps(out, default=str))
        else:
            for e in out:
                f = e["fact"]
                click.echo(f"[{f['confidence']}] {f['support_span']}")
                if e["chunk"]:
                    click.echo(f"    ↳ doc={e['doc_id']} chunk_seq={e['chunk']['seq_num']}")
                if e.get("summary"):
                    click.echo(f"    summary: {e['summary']}")
    except Exception as exc:  # actionable, no traceback (matches `search`)
        raise click.ClickException(str(exc))
```

Confirm `cfg.target.dsn` is the right accessor by checking how the existing `search` command obtains the DSN (it may resolve via `tgt`/`_DsnResolvable`); reuse that exact accessor.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync pytest tests/chunkshop/test_fact_search.py -v`
Expected: `test_fact_search_command_registered` PASS; integration test PASS if `CHUNKSHOP_TEST_DSN` is set, else SKIP.

- [ ] **Step 6: Search-pollution regression (DB) — normal search excludes facts**

```python
# append to tests/chunkshop/test_fact_search.py
@pytest.mark.skipif(not DSN, reason="CHUNKSHOP_TEST_DSN not set")
def test_normal_search_excludes_facts_by_default(tmp_path):
    from click.testing import CliRunner
    from chunkshop.cli import cli
    cfg_path = _write_memory_cell_yaml(tmp_path, DSN)
    _ingest_one_fact_doc(cfg_path)
    res = CliRunner().invoke(cli, ["search", "--config", str(cfg_path),
                                   "--query", "alpha", "--json"])
    assert res.exit_code == 0, res.output
    import json
    hits = json.loads(res.output)
    assert all(h.get("metadata", {}).get("kind") != "fact" for h in hits)
```

Run: `uv run --no-sync pytest tests/chunkshop/test_fact_search.py -v`
Expected: PASS or SKIP (no errors).

- [ ] **Step 7: Commit**

```bash
git add src/chunkshop/cli.py tests/chunkshop/test_fact_search.py
git commit -m "feat(cli): fact-search command with chunk/doc breadcrumb"
```

**End of Phase C — fact-search ships.**

---

## Final verification

- [ ] **Run the full suite**

Run: `uv run --no-sync pytest -q`
Expected: all green; spaCy/lede-gated tests skip cleanly if extras absent.

- [ ] **Docs touch-up (optional, if time permits)**

Add a short `consolidator: { mode: lede_spacy }` example to `docs/` memory/consolidation reference and mention `chunkshop fact-search` + `--compress`. Not required for the feature to function.

---

## Self-Review (completed by plan author)

**Spec coverage:** D1 (B1/B2 + LLM excluded), D2 hybrid (B3 keeps CallableConsolidator), D3 caveman-as-summarizer (A1), D4 read-time off-by-default (A2/A3), D5 decoupled slots (B4), D6 co-located (no table task), D7 kind-aware defaults + `--include-facts` (C2) via `metadata_not` (C1), D8 per-extractor confidence (B1 rank-decay / B2 heuristic), D9 extras gating (lazy imports + skip-not-fail in B1/B2), D10 confidence_floor (B3 config + B4 filter). fact-search #2 (C3). Concern A regression (C3 Step 6). All covered.

**Placeholder scan:** No TBD/TODO. The only soft spots are the two integration-test fixture helpers in C3 (`_write_memory_cell_yaml`, `_ingest_one_fact_doc`), which are explicitly delegated to mirroring existing memory-cell fixtures because their exact shape depends on current MemorySink test scaffolding — flagged inline, not hidden.

**Type consistency:** `extract_facts(text, **kwargs) -> list[dict]` used identically in B1/B2/B4. Fact dict keys `{subject, predicate, object, support_span, confidence}` consistent across extractors, `_normalize`, and `fact-search`. `compress_fn` signature `(str) -> str` consistent A2/A3. `metadata_not` dict shape consistent C1/C2.
