# lede v0.4 Hint-Biased Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire lede v0.4's hint-biased extraction into chunkshop across four tiers — enable+document (L1), per-document hints (L2), a `top_terms` extractor (L3), and lede-spacy hint expansion (L4) — without breaking byte-identical no-hints behavior.

**Architecture:** chunkshop already forwards `**kwargs` through `chunkshop.summarizers.lede.summarize`, so hint kwargs flow with no shim change (L1). L2 adds three `*_from_meta` pointer fields on `CallableSummarizer` and resolves them in `chunkers/_summarizer.py`. L3 adds a new `Extractor`-protocol module + discriminated-union config branch. L4 adds a third lazy-import shim (`chunkshop/hints.py`) wrapping `lede_spacy.expand_hints`, plus an `expand:` config block on the summarizer and the extractor. lede/lede_spacy are imported lazily inside exactly three designated shim files; a grep test enforces that boundary.

**Tech Stack:** Python 3.12, pydantic v2 (`extra="forbid"` discriminated unions), pytest, `uv`. Runtime deps: `lede>=0.4` (PyPI, confirmed published), `lede-spacy>=0.4` (PyPI, confirmed published; pulls spaCy). lede-spacy / `expand_hints` is **Python-only by lede's design** — there is no Rust equivalent and the Rust port (`rust/`) is untouched; the `[lede-spacy]` extra stays strictly optional.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-lede-v04-hints.md` — this plan implements SC-001..SC-023 and injects DC-001/DC-002/DC-003/DC-FINAL as hard gates. Re-read the brief at each ⛔ gate.

**Sequencing:** L1 (Tasks 1–6) → ⛔ DC-001 → L2 (Tasks 7–8) → ⛔ DC-002 → L3 (Tasks 9–10) → L4 (Tasks 11–14) → ⛔ DC-003 → ⛔ DC-FINAL.

**Working directory:** all commands run from `python/` unless noted. Install once before starting:

```bash
cd python
uv sync --extra dev --extra extractors --extra all-backends
uv pip install "lede>=0.4" "lede-spacy>=0.4"
uv run python -m spacy download en_core_web_sm   # for the L4 lemma test
```

**Resolved facts (no longer open):**
- lede 0.4.0 and lede-spacy 0.4.0 are both on PyPI — SC-001/SC-017 use plain PyPI pins; the editable-path fallback clause is moot.
- `ExtractResult.tags` is `list[str]` (see `extractors/result.py`), not a tuple — the brief's "tuple" wording yields to the real dataclass.
- The summarizer callable is invoked as `self._summarize(bc.original_content, doc_meta)` in `chunkers/summary_embed.py:34`, where `doc_meta = dict(doc.metadata or {})`. So L2's `*_from_meta` reads from **document** metadata.

---

## File Structure

**Created:**
- `python/src/chunkshop/hints.py` — L4 expand_hints passthrough shim (lazy `lede_spacy` import).
- `python/src/chunkshop/extractors/lede_top_terms.py` — L3 extractor (lazy `lede.extract.top_terms` import).
- `python/tests/chunkshop/test_lede_hints_e2e.py` — L1 e2e (SC-005).
- `python/tests/chunkshop/test_lede_no_hints_byte_identical.py` — backward-compat golden (SC-015).
- `python/tests/chunkshop/test_no_lede_core_imports.py` — import-boundary guard (SC-016).
- `python/tests/chunkshop/test_summarizer_hints_from_meta.py` — L2 (SC-009).
- `python/tests/chunkshop/test_lede_top_terms_extractor.py` — L3 (SC-014).
- `python/tests/chunkshop/test_hint_expansion.py` — L4 (SC-022).
- `docs/samples/sample-summary-embed-hints.yaml` — sample (SC-002).

**Modified:**
- `python/pyproject.toml` — extras (SC-001, SC-017).
- `python/src/chunkshop/config.py` — `*_from_meta` fields (SC-006), `HintExpansion` (SC-019), `expand` fields (SC-020/021), `LedeTopTermsExtractor` + union (SC-011).
- `python/src/chunkshop/chunkers/_summarizer.py` — per-doc resolution + expansion (SC-007, SC-008, SC-020).
- `python/src/chunkshop/extractors/__init__.py` — loader branch (SC-012).
- `docs/quickstart-summaries.md` — hints + expansion docs (SC-003, SC-023).
- `docs/tutorial-summaries.md` — per-doc hints worked example (SC-004).

---

## Tier L1 — Enable & Document

### Task 1: Bump extras for lede v0.4 + add lede-spacy extra

**Files:**
- Modify: `python/pyproject.toml:34`

- [ ] **Step 1: Edit the extras block**

Change line `lede = ["lede>=0.3"]` and add the new extra directly below it:

```toml
lede = ["lede>=0.4"]
lede-spacy = ["lede-spacy>=0.4"]
```

Leave the `summarize = []` umbrella comment as-is.

- [ ] **Step 2: Resolve and smoke-test (SC-001, SC-017)**

Run:
```bash
uv pip install -e ".[lede]" -e ".[lede-spacy]"
uv run python -c "import lede; print('lede', lede.__version__)"
uv run python -c "import lede_spacy, spacy; print('lede_spacy ok; spacy', spacy.__version__)"
```
Expected: lede ≥ 0.4.0; lede_spacy imports; spaCy version in `[3.8, 3.9)`. Record the resolved spaCy version in the commit message (SC-017 documents the `[nlp]` `spacy>=3.7` vs lede-spacy `spacy>=3.8,<3.9` co-install constraint — do NOT edit the `[nlp]` pin).

- [ ] **Step 3: Commit**

```bash
git add python/pyproject.toml
git commit -m "build(deps): bump [lede] to >=0.4, add optional [lede-spacy] extra"
```

---

### Task 2: L1 e2e — hint kwargs flow through the shim (SC-005)

**Files:**
- Test: `python/tests/chunkshop/test_lede_hints_e2e.py`

- [ ] **Step 1: Write the failing test**

```python
"""L1 e2e (SC-005): hint kwargs flow text -> summary_embed -> lede, biasing output."""
import importlib.util

import pytest

from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.chunkers.sentence_aware import SentenceAwareChunker
from chunkshop.config import SummaryEmbedChunker as SummaryEmbedCfg
from chunkshop.config import SentenceAwareChunker as SentenceAwareCfg
from chunkshop.sources.base import Document


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


# A doc with one clearly hint-bearing sentence and several distractor sentences.
TEXT = (
    "The annual budget was approved last spring. "
    "John Smith lives in Cook County and runs a small hardware business. "
    "The weather has been mild this year. "
    "Several committees met to discuss zoning. "
    "Trade volumes rose in the third quarter."
)


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_hard_hint_biases_summary():
    cfg = SummaryEmbedCfg(
        type="summary_embed",
        base=SentenceAwareCfg(type="sentence_aware"),
        summarizer={
            "mode": "callable",
            "module": "chunkshop.summarizers.lede",
            "function": "summarize",
            "kwargs": {
                "max_length": 200,
                "hints": ["John Smith"],
                "hint_focus": 1.0,
                "hint_mode": "hard",
            },
        },
    )
    base = SentenceAwareChunker(cfg.base)
    chunker = SummaryEmbedChunker(cfg, base)
    doc = Document(id="d1", content=TEXT, metadata={})
    chunks = chunker.chunk(doc)
    joined = " ".join(c.embedded_content for c in chunks)
    assert "John Smith" in joined, f"hard hint did not bias output: {joined!r}"
```

- [ ] **Step 2: Run to verify it passes (no production change needed — proves the existing passthrough already works)**

Run: `uv run pytest tests/chunkshop/test_lede_hints_e2e.py -v`
Expected: PASS (the shim's `**kwargs` already forwards `hints`/`hint_focus`/`hint_mode`). If it FAILS, stop — the passthrough assumption in the brief is wrong and L1 needs a shim fix.

- [ ] **Step 3: Commit**

```bash
git add python/tests/chunkshop/test_lede_hints_e2e.py
git commit -m "test(lede): e2e proves hint kwargs flow through summary_embed shim (SC-005)"
```

---

### Task 3: Backward-compat byte-identical golden (SC-015)

**Files:**
- Test: `python/tests/chunkshop/test_lede_no_hints_byte_identical.py`

- [ ] **Step 1: Capture the golden string (run once, against current code)**

Run:
```bash
uv run python -c "
from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.chunkers.sentence_aware import SentenceAwareChunker
from chunkshop.config import SummaryEmbedChunker as C, SentenceAwareChunker as S
from chunkshop.sources.base import Document
TEXT='The annual budget was approved last spring. John Smith lives in Cook County and runs a small hardware business. The weather has been mild this year. Several committees met to discuss zoning. Trade volumes rose in the third quarter.'
cfg=C(type='summary_embed', base=S(type='sentence_aware'), summarizer={'mode':'callable','module':'chunkshop.summarizers.lede','function':'summarize','kwargs':{'max_length':200}})
ch=SummaryEmbedChunker(cfg, SentenceAwareChunker(cfg.base))
out=' '.join(c.embedded_content for c in ch.chunk(Document(id='d1', content=TEXT, metadata={})))
print(repr(out))
"
```
Copy the printed `repr(...)` value into `GOLDEN` in Step 2.

- [ ] **Step 2: Write the test with the captured golden**

```python
"""Backward-compat (SC-015): no-hints output is byte-identical to the captured golden.

The golden is captured ONCE (see plan Task 3 Step 1). Do NOT regenerate it
without an explicit instruction — a diff here means the no-hints path changed.
"""
import importlib.util

import pytest

from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.chunkers.sentence_aware import SentenceAwareChunker
from chunkshop.config import SummaryEmbedChunker as C, SentenceAwareChunker as S
from chunkshop.sources.base import Document


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


TEXT = (
    "The annual budget was approved last spring. "
    "John Smith lives in Cook County and runs a small hardware business. "
    "The weather has been mild this year. "
    "Several committees met to discuss zoning. "
    "Trade volumes rose in the third quarter."
)

# <<< paste the repr() output from Task 3 Step 1 here >>>
GOLDEN = "PASTE_GOLDEN_HERE"


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_no_hints_byte_identical():
    cfg = C(
        type="summary_embed",
        base=S(type="sentence_aware"),
        summarizer={
            "mode": "callable",
            "module": "chunkshop.summarizers.lede",
            "function": "summarize",
            "kwargs": {"max_length": 200},
        },
    )
    ch = SummaryEmbedChunker(cfg, SentenceAwareChunker(cfg.base))
    out = " ".join(c.embedded_content for c in ch.chunk(Document(id="d1", content=TEXT, metadata={})))
    assert out == GOLDEN
```

- [ ] **Step 3: Run to verify it passes**

Run: `uv run pytest tests/chunkshop/test_lede_no_hints_byte_identical.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add python/tests/chunkshop/test_lede_no_hints_byte_identical.py
git commit -m "test(lede): pin no-hints byte-identical golden (SC-015)"
```

---

### Task 4: Import-boundary guard (SC-016)

**Files:**
- Test: `python/tests/chunkshop/test_no_lede_core_imports.py`

- [ ] **Step 1: Write the test (allowlists all three shim files up front, including L3/L4 files not yet created)**

```python
"""SC-016: only the three designated shim files may import lede / lede_spacy."""
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "chunkshop"

ALLOWED = {
    SRC / "summarizers" / "lede.py",
    SRC / "extractors" / "lede_top_terms.py",
    SRC / "hints.py",
}

IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+lede(?:_spacy)?\b", re.MULTILINE)


def test_no_lede_imports_in_core():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        if IMPORT_RE.search(text):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"lede/lede_spacy imported outside shim files: {offenders}"
```

- [ ] **Step 2: Run to verify it passes**

Run: `uv run pytest tests/chunkshop/test_no_lede_core_imports.py -v`
Expected: PASS (no core file imports lede today; `summarizers/lede.py` is allowlisted).

- [ ] **Step 3: Commit**

```bash
git add python/tests/chunkshop/test_no_lede_core_imports.py
git commit -m "test: guard lede/lede_spacy import boundary to three shim files (SC-016)"
```

---

### Task 5: Sample YAML (SC-002)

**Files:**
- Create: `docs/samples/sample-summary-embed-hints.yaml`

- [ ] **Step 1: Write the sample (two cells: static hints, and hints_from_meta)**

```yaml
# Hint-biased summary_embed (lede v0.4). Two cells demonstrating static hints
# and per-document hints. Requires the [lede] extra. See docs/quickstart-summaries.md.
cells:
  - name: static-hints
    source:
      type: files
      glob: ../docs/samples/handbook-*.md
    chunker:
      type: summary_embed
      base:
        type: hierarchy
      summarizer:
        mode: callable
        module: chunkshop.summarizers.lede
        function: summarize
        kwargs:
          max_length: 400
          hints: ["onboarding", "benefits"]
          hint_focus: 0.7
          hint_mode: soft
    embedder:
      type: fastembed
      model: BAAI/bge-small-en-v1.5
    target:
      type: pg
      dsn_env: CHUNKSHOP_TEST_DSN
      table: chunkshop_samples_hints_static
      mode: overwrite
      source_tag: hints_static

  - name: per-doc-hints
    source:
      type: files
      glob: ../docs/samples/handbook-*.md
    chunker:
      type: summary_embed
      base:
        type: hierarchy
      summarizer:
        mode: callable
        module: chunkshop.summarizers.lede
        function: summarize
        kwargs:
          max_length: 400
          hint_focus: 0.7
          hint_mode: soft
        hints_from_meta: lede_hints
    embedder:
      type: fastembed
      model: BAAI/bge-small-en-v1.5
    target:
      type: pg
      dsn_env: CHUNKSHOP_TEST_DSN
      table: chunkshop_samples_hints_perdoc
      mode: overwrite
      source_tag: hints_perdoc
```

> NOTE: `hints_from_meta` is added to the config model in Task 7. Until Task 7 lands, `chunkshop validate` will reject the `per-doc-hints` cell. Sequence: validate the static cell now; re-validate the whole file at DC-002.

- [ ] **Step 2: Validate the static cell**

Temporarily comment out the `per-doc-hints` cell, then run:
```bash
cd python && uv run chunkshop validate --config ../docs/samples/sample-summary-embed-hints.yaml
```
Expected: exit 0. Then un-comment the cell (it validates after Task 7).

- [ ] **Step 3: Commit**

```bash
git add docs/samples/sample-summary-embed-hints.yaml
git commit -m "docs(samples): add hint-biased summary_embed sample (SC-002)"
```

---

### Task 6: Quickstart + tutorial hint docs (SC-003, SC-004)

**Files:**
- Modify: `docs/quickstart-summaries.md`
- Modify: `docs/tutorial-summaries.md`

- [ ] **Step 1: Add the quickstart "Hint-biased extraction (v0.4)" section**

Append to `docs/quickstart-summaries.md` a section covering: a minimal `summary_embed` + lede config with `kwargs.hints`; soft vs hard mode (soft biases ranking, hard filters to hint-bearing sentences); list-vs-dict hints (`["a","b"]` vs `{"a": 2.0, "b": 1.0}`); and the four matching rules verbatim:

```markdown
## Hint-biased extraction (lede v0.4)

Pass `hints` (plus optional `hint_focus`, `hint_mode`) under `summarizer.kwargs`
to bias the extractive summary toward specific terms:

    chunker:
      type: summary_embed
      base: {type: hierarchy}
      summarizer:
        mode: callable
        module: chunkshop.summarizers.lede
        kwargs:
          hints: ["onboarding", "benefits"]   # or {term: weight}
          hint_focus: 0.7                      # 0=ignore .. 1=only-hint pool
          hint_mode: soft                      # soft=bias, hard=filter

Matching rules (from lede): case-insensitive; word-boundary (`smith` does not
match `blacksmith`); multi-word hints are contiguous (`John Smith` matches
`John Smith Sr.` but not `John P. Smith`); no Unicode normalization
(`café` ≠ `cafe`). No stemming — see "Hint expansion" below.
```

- [ ] **Step 2: Add the tutorial per-doc-hints worked example (SC-004)**

Append to `docs/tutorial-summaries.md` an example showing a `files` source whose docs carry a `lede_hints` metadata field, a `summary_embed` cell with `hints_from_meta: lede_hints`, and a note that present metadata overrides static `kwargs.hints` per document (forward-references Task 7).

- [ ] **Step 3: Commit**

```bash
git add docs/quickstart-summaries.md docs/tutorial-summaries.md
git commit -m "docs(summaries): document hint-biased extraction + per-doc hints (SC-003, SC-004)"
```

---

### ⛔ DC-001 — Gate before L2

- [ ] **Re-read** `skill-output/mission-brief/Mission-Brief-lede-v04-hints.md`.
- [ ] Verify evidence for SC-001, SC-002 (static cell), SC-003, SC-004, SC-005, SC-015, SC-016, SC-017. Run:
```bash
cd python && uv run pytest tests/chunkshop/test_lede_hints_e2e.py tests/chunkshop/test_lede_no_hints_byte_identical.py tests/chunkshop/test_no_lede_core_imports.py -v
```
Expected: all PASS.
- [ ] Drift check: still solving the brief's Purpose? Each L1 task maps to an SC? Nothing from Out of Scope touched? If any L1 SC lacks evidence, fix before L2.

---

## Tier L2 — Per-Document Hints

### Task 7: Add `*_from_meta` fields to CallableSummarizer (SC-006)

**Files:**
- Modify: `python/src/chunkshop/config.py:285-290`
- Test: `python/tests/chunkshop/test_config_summarizer.py`

- [ ] **Step 1: Write the failing config test**

Add to `tests/chunkshop/test_config_summarizer.py`:

```python
def test_callable_summarizer_from_meta_fields_default_none():
    from chunkshop.config import CallableSummarizer
    cfg = CallableSummarizer(mode="callable", module="m", function="summarize")
    assert cfg.hints_from_meta is None
    assert cfg.hint_focus_from_meta is None
    assert cfg.hint_mode_from_meta is None


def test_callable_summarizer_rejects_unknown_field():
    import pytest
    from pydantic import ValidationError
    from chunkshop.config import CallableSummarizer
    with pytest.raises(ValidationError):
        CallableSummarizer(mode="callable", module="m", hints_from_metaa="x")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/chunkshop/test_config_summarizer.py::test_callable_summarizer_from_meta_fields_default_none -v`
Expected: FAIL (`AttributeError`/`ValidationError` — field doesn't exist).

- [ ] **Step 3: Add the three fields**

In `config.py`, edit `CallableSummarizer`:

```python
class CallableSummarizer(_Base):
    """Import a module lazily at first use; call ``function(text, **kwargs) -> str``."""
    mode: Literal["callable"]
    module: str
    function: str = "summarize"
    kwargs: dict = Field(default_factory=dict)
    hints_from_meta: Optional[str] = None
    hint_focus_from_meta: Optional[str] = None
    hint_mode_from_meta: Optional[str] = None
```

- [ ] **Step 4: Run to verify both tests pass**

Run: `uv run pytest tests/chunkshop/test_config_summarizer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_config_summarizer.py
git commit -m "feat(config): add *_from_meta pointer fields to CallableSummarizer (SC-006)"
```

---

### Task 8: Per-doc resolution + type validation in dispatch (SC-007, SC-008, SC-009)

**Files:**
- Modify: `python/src/chunkshop/chunkers/_summarizer.py:53-71`
- Test: `python/tests/chunkshop/test_summarizer_hints_from_meta.py`

- [ ] **Step 1: Write the failing tests**

```python
"""L2 (SC-009): per-doc hints override static; absent falls back; wrong type raises."""
import pytest

from chunkshop.chunkers._summarizer import build_summarizer
from chunkshop.config import CallableSummarizer


# A fake summarizer module-level fn that just echoes the kwargs it received.
_SEEN = {}


def _spy(text, **kwargs):
    _SEEN.clear()
    _SEEN.update(kwargs)
    return text


def _cfg(**extra):
    return CallableSummarizer(
        mode="callable",
        module="tests.chunkshop.test_summarizer_hints_from_meta",
        function="_spy",
        kwargs={"hints": ["static"], "hint_focus": 0.5},
        **extra,
    )


def test_perdoc_hints_override_static():
    fn = build_summarizer(_cfg(hints_from_meta="lede_hints"))
    fn("body", {"lede_hints": ["perdoc"]})
    assert _SEEN["hints"] == ["perdoc"]


def test_absent_meta_falls_back_to_static():
    fn = build_summarizer(_cfg(hints_from_meta="lede_hints"))
    fn("body", {})  # no lede_hints key
    assert _SEEN["hints"] == ["static"]


def test_wrong_type_raises_with_field_name():
    fn = build_summarizer(_cfg(hint_focus_from_meta="focus"))
    with pytest.raises(RuntimeError) as exc:
        fn("body", {"focus": "not-a-float"})
    assert "focus" in str(exc.value)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/chunkshop/test_summarizer_hints_from_meta.py -v`
Expected: FAIL (per-doc resolution not implemented; `_SEEN["hints"]` stays `["static"]`).

- [ ] **Step 3: Implement resolution in `_callable`**

Replace the `CallableSummarizer` branch body in `_summarizer.py` (the `kwargs = dict(cfg.kwargs)` + `_callable` definition) with:

```python
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
            if focus is not None and not isinstance(focus, bool):
                call_kwargs["hint_focus"] = float(focus)
            elif isinstance(focus, bool):
                raise RuntimeError(
                    f"callable summarizer: doc.metadata[{cfg.hint_focus_from_meta!r}] "
                    f"(for hint_focus) must be a number, got bool"
                )
            mode = _resolve(meta, cfg.hint_mode_from_meta, "hint_mode",
                            (str,), "a string")
            if mode is not None:
                call_kwargs["hint_mode"] = mode
            return fn(text, **call_kwargs)

        return _callable
```

- [ ] **Step 4: Run to verify the L2 tests pass**

Run: `uv run pytest tests/chunkshop/test_summarizer_hints_from_meta.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/chunkers/_summarizer.py python/tests/chunkshop/test_summarizer_hints_from_meta.py
git commit -m "feat(summarizer): resolve per-doc hints from metadata, override static (SC-007, SC-008, SC-009)"
```

---

### ⛔ DC-002 — Gate before L3

- [ ] **Re-read** the mission brief.
- [ ] Verify SC-006..SC-009 evidence (config fields + dispatch + L2 tests pass).
- [ ] **Re-run SC-015 and SC-016** — Task 8 touched `_summarizer.py`, so confirm the no-hints golden and the import boundary still hold:
```bash
cd python && uv run pytest tests/chunkshop/test_lede_no_hints_byte_identical.py tests/chunkshop/test_no_lede_core_imports.py tests/chunkshop/test_summarizer_hints_from_meta.py -v
```
Expected: all PASS.
- [ ] Re-validate the full sample (both cells now valid):
```bash
cd python && uv run chunkshop validate --config ../docs/samples/sample-summary-embed-hints.yaml
```
Expected: exit 0.
- [ ] Drift check (Purpose / SC mapping / Out of Scope). Fix any gap before L3.

---

## Tier L3 — top_terms Extractor

### Task 9: LedeTopTermsExtractor config model + union + loader (SC-011, SC-012)

**Files:**
- Modify: `python/src/chunkshop/config.py:598-613`
- Modify: `python/src/chunkshop/extractors/__init__.py`
- Test: `python/tests/chunkshop/test_config_summarizer.py` (or a new `test_config_extractors.py`)

- [ ] **Step 1: Write the failing config test**

Add to `tests/chunkshop/test_config_summarizer.py`:

```python
def test_lede_top_terms_config_defaults_and_discriminates():
    from chunkshop.config import CompositeExtractor
    comp = CompositeExtractor(type="composite", extractors=[{"type": "lede_top_terms"}])
    e = comp.extractors[0]
    assert e.type == "lede_top_terms"
    assert e.n == 10
    assert e.kinds == ("words", "phrases")
    assert e.hint_focus == 0.7
    assert e.hint_mode == "soft"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/chunkshop/test_config_summarizer.py::test_lede_top_terms_config_defaults_and_discriminates -v`
Expected: FAIL (unknown discriminator `lede_top_terms`).

- [ ] **Step 3: Add the model + extend the union**

In `config.py`, add after `SpacyEntitiesExtractor` (before `CompositeExtractor`):

```python
class LedeTopTermsExtractor(_Base):
    type: Literal["lede_top_terms"]
    n: int = Field(default=10, ge=1)
    kinds: tuple[Literal["words", "phrases"], ...] = ("words", "phrases")
    hints: Optional[Union[list[str], dict[str, float]]] = None
    hint_focus: float = Field(default=0.7, ge=0.0, le=1.0)
    hint_mode: Literal["soft", "hard"] = "soft"

    @field_validator("kinds")
    @classmethod
    def _kinds_nonempty(cls, v):
        if not v:
            raise ValueError("kinds must be non-empty")
        return v
```

Then add `LedeTopTermsExtractor,` to the `Union[...]` in `ExtractorConfig` (between `SpacyEntitiesExtractor` and `CompositeExtractor`).

- [ ] **Step 4: Add the loader branch**

In `extractors/__init__.py`, import `LedeTopTermsExtractor as LedeTopTermsCfg` from `chunkshop.config`, import `LedeTopTermsExtractor` from `chunkshop.extractors.lede_top_terms` (created in Task 10), and add before the final `raise`:

```python
    if isinstance(cfg, LedeTopTermsCfg):
        return LedeTopTermsExtractor(cfg)
```

> The runtime import of the extractor class is fine — `lede_top_terms.py` itself imports `lede` only inside `extract()`, so importing the module does not import lede.

- [ ] **Step 5: Run to verify config test passes (extractor module lands in Task 10; loader import will fail until then — sequence Task 10 immediately after)**

Run: `uv run pytest tests/chunkshop/test_config_summarizer.py::test_lede_top_terms_config_defaults_and_discriminates -v`
Expected: PASS for the config test. (If the `extractors/__init__.py` import of the not-yet-created class breaks collection, complete Task 10 Step 1–3 first, then run.)

- [ ] **Step 6: Commit (after Task 10 so the package imports cleanly)** — defer commit to Task 10 Step 6.

---

### Task 10: lede_top_terms extractor module (SC-010, SC-013, SC-014)

**Files:**
- Create: `python/src/chunkshop/extractors/lede_top_terms.py`
- Test: `python/tests/chunkshop/test_lede_top_terms_extractor.py`

- [ ] **Step 1: Write the failing tests**

```python
"""L3 (SC-014): top_terms extractor — kinds filtering, hint bias, lazy-import error."""
import importlib.util

import pytest

from chunkshop.config import LedeTopTermsExtractor as Cfg
from chunkshop.extractors.lede_top_terms import LedeTopTermsExtractor


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


TEXT = (
    "John Smith attended the Cook County council meeting about property taxes. "
    "The council debated zoning, taxes, and the county budget at length. "
    "Property taxes and the county budget dominated the discussion."
)


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_words_only_kind():
    r = LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=5, kinds=("words",))).extract(TEXT)
    assert all(t["kind"] == "word" for t in r.metadata["top_terms"])
    assert len(r.tags) <= 5
    assert list(r.tags) == [t["term"] for t in r.metadata["top_terms"]]


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_phrases_only_kind():
    r = LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=5, kinds=("phrases",))).extract(TEXT)
    assert all(t["kind"] == "phrase" for t in r.metadata["top_terms"])


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_hard_hint_biases_terms():
    base = LedeTopTermsExtractor(Cfg(type="lede_top_terms", n=8)).extract(TEXT)
    hinted = LedeTopTermsExtractor(
        Cfg(type="lede_top_terms", n=8, hints=["taxes"], hint_mode="hard")
    ).extract(TEXT)
    assert any("tax" in t.lower() for t in hinted.tags)


def test_missing_lede_raises_actionable(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("lede"):
            raise ImportError("no lede")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError) as exc:
        LedeTopTermsExtractor(Cfg(type="lede_top_terms")).extract(TEXT)
    assert "lede" in str(exc.value).lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/chunkshop/test_lede_top_terms_extractor.py -v`
Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Write the extractor**

```python
"""lede_top_terms extractor — ranked salient words/phrases via lede.extract.top_terms.

lede is imported lazily inside extract() so chunkshop core never imports lede
(SC-016). top_terms is Python-only in lede v0.4 (Rust mirror deferred to v0.5).
"""
from __future__ import annotations

from chunkshop.config import LedeTopTermsExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class LedeTopTermsExtractor:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def extract(self, text: str) -> ExtractResult:
        try:
            from lede.extract import top_terms
        except ImportError as exc:
            raise RuntimeError(
                "lede_top_terms extractor requires lede>=0.4. "
                "Install with: pip install 'lede>=0.4' (or the chunkshop [lede] extra)."
            ) from exc

        results = top_terms(
            text,
            n=self.cfg.n,
            kinds=tuple(self.cfg.kinds),
            hints=self.cfg.hints,
            hint_focus=self.cfg.hint_focus,
            hint_mode=self.cfg.hint_mode,
        )
        # lede returns ranked (term, score, kind)-shaped entries; normalize to
        # our metadata contract. Probe the shape once and adapt.
        entries = []
        for item in results:
            term, score, kind = _unpack(item)
            entries.append({"term": term, "score": float(score), "kind": kind})
        tags = [e["term"] for e in entries]
        return ExtractResult(tags=tags, metadata={"top_terms": entries})


def _unpack(item):
    """Normalize a lede top_terms entry to (term, score, kind).

    lede v0.4 returns a tuple of term strings from the simple call and richer
    objects from the ranked call; this adapter handles both tuple- and
    attribute-shaped entries so the extractor is robust to lede's exact return.
    """
    if isinstance(item, str):
        return item, 0.0, "word"
    if isinstance(item, (tuple, list)):
        term = item[0]
        score = item[1] if len(item) > 1 else 0.0
        kind = item[2] if len(item) > 2 else "word"
        return term, score, kind
    # attribute-shaped
    return (
        getattr(item, "term", str(item)),
        getattr(item, "score", 0.0),
        getattr(item, "kind", "word"),
    )
```

> **Implementation note for the worker:** before finalizing, confirm lede v0.4's exact `top_terms` return shape with `uv run python -c "from lede.extract import top_terms; print(top_terms('a b a c', n=3, kinds=('words',)))"` and tighten `_unpack` if the real shape is known (the doc example shows a tuple of strings; the ranked form may carry scores). The adapter above is intentionally defensive; replace with the exact shape once observed. Cite the observed shape in the commit message (per cite-your-sources).

- [ ] **Step 4: Run to verify the L3 tests pass**

Run: `uv run pytest tests/chunkshop/test_lede_top_terms_extractor.py -v`
Expected: PASS (the lede-gated tests pass; `test_missing_lede_raises_actionable` always runs).

- [ ] **Step 5: Run the existing extractor suite (SC-012 — no regression)**

Run: `uv run pytest tests/chunkshop/ -k extractor -v`
Expected: PASS.

- [ ] **Step 6: Commit (Tasks 9 + 10 together)**

```bash
git add python/src/chunkshop/config.py python/src/chunkshop/extractors/__init__.py python/src/chunkshop/extractors/lede_top_terms.py python/tests/chunkshop/test_lede_top_terms_extractor.py python/tests/chunkshop/test_config_summarizer.py
git commit -m "feat(extractors): add lede_top_terms extractor + config branch (SC-010..SC-014)"
```

---

## Tier L4 — Hint Expansion (lede-spacy)

### Task 11: HintExpansion model (SC-019)

**Files:**
- Modify: `python/src/chunkshop/config.py` (near the summarizer models)
- Test: `python/tests/chunkshop/test_config_summarizer.py`

- [ ] **Step 1: Write the failing test**

```python
def test_hint_expansion_defaults_and_validation():
    import pytest
    from pydantic import ValidationError
    from chunkshop.config import HintExpansion
    h = HintExpansion()
    assert h.kinds == ("lemma",)
    assert h.top_k == 5
    assert h.expand_weight == 0.5
    with pytest.raises(ValidationError):
        HintExpansion(kinds=())          # empty
    with pytest.raises(ValidationError):
        HintExpansion(kinds=("bogus",))  # bad literal
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/chunkshop/test_config_summarizer.py::test_hint_expansion_defaults_and_validation -v`
Expected: FAIL (no `HintExpansion`).

- [ ] **Step 3: Add the model**

In `config.py`, above `CallableSummarizer`:

```python
class HintExpansion(_Base):
    """Optional lede-spacy hint expansion. lemma is cheap; synonyms/similar
    require extra installs (enforced at runtime in chunkshop/hints.py)."""
    kinds: tuple[Literal["lemma", "synonyms", "similar"], ...] = ("lemma",)
    top_k: int = Field(default=5, ge=1)
    expand_weight: float = Field(default=0.5, ge=0.0)

    @field_validator("kinds")
    @classmethod
    def _kinds_nonempty(cls, v):
        if not v:
            raise ValueError("kinds must be non-empty")
        return v
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/chunkshop/test_config_summarizer.py::test_hint_expansion_defaults_and_validation -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_config_summarizer.py
git commit -m "feat(config): add HintExpansion model (SC-019)"
```

---

### Task 12: chunkshop/hints.py shim (SC-018)

**Files:**
- Create: `python/src/chunkshop/hints.py`
- Test: `python/tests/chunkshop/test_hint_expansion.py`

- [ ] **Step 1: Write the failing tests**

```python
"""L4 (SC-018, SC-022): expand_hints shim + wrapped errors."""
import importlib.util

import pytest

from chunkshop.hints import expand_hints


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _has_spacy_model(name="en_core_web_sm") -> bool:
    if not _has("spacy"):
        return False
    import spacy
    try:
        spacy.load(name)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_spacy_model(), reason="no spaCy model installed")
def test_lemma_expansion_adds_lemmatized_form():
    out = expand_hints(["counties"], kinds=("lemma",))
    assert "counties" in out
    assert any(t.lower() == "county" for t in out)


def test_synonyms_without_extra_raises_actionable(monkeypatch):
    # Simulate lede_spacy raising ImportError for the synonyms path.
    import chunkshop.hints as H

    def boom(*a, **k):
        raise ImportError("nltk missing")

    monkeypatch.setattr(H, "_load_expand", lambda: boom)
    with pytest.raises(RuntimeError) as exc:
        expand_hints(["car"], kinds=("synonyms",))
    msg = str(exc.value).lower()
    assert "synonyms" in msg or "nltk" in msg or "lede-spacy" in msg
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/chunkshop/test_hint_expansion.py -v`
Expected: FAIL (no `chunkshop.hints`).

- [ ] **Step 3: Write the shim**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/chunkshop/test_hint_expansion.py -v`
Expected: PASS (lemma test skips if no model; synonyms test passes via the monkeypatched error path).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/hints.py python/tests/chunkshop/test_hint_expansion.py
git commit -m "feat(hints): add lede-spacy expand_hints shim with actionable errors (SC-018)"
```

---

### Task 13: Wire `expand` into summarizer + extractor (SC-020, SC-021, SC-022)

**Files:**
- Modify: `python/src/chunkshop/config.py` (`CallableSummarizer`, `LedeTopTermsExtractor`)
- Modify: `python/src/chunkshop/chunkers/_summarizer.py`
- Modify: `python/src/chunkshop/extractors/lede_top_terms.py`
- Test: `python/tests/chunkshop/test_hint_expansion.py` (append)

- [ ] **Step 1: Write the failing tests (append)**

```python
def test_summarizer_expand_runs_on_resolved_hints():
    from chunkshop.chunkers._summarizer import build_summarizer
    from chunkshop.config import CallableSummarizer
    seen = {}

    import chunkshop.chunkers._summarizer as M
    # Stub expand_hints used by the dispatch so the test needs no spaCy model.
    M_expand = getattr(M, "expand_hints", None)

    cfg = CallableSummarizer(
        mode="callable",
        module="tests.chunkshop.test_hint_expansion",
        function="_echo",
        kwargs={"hints": ["a"]},
        expand={"kinds": ["lemma"]},
        hints_from_meta="lede_hints",
    )
    fn = build_summarizer(cfg)
    fn("body", {"lede_hints": ["counties"]})
    # The per-doc hint ["counties"] is what gets expanded, not ["a"].
    assert _ECHO["expanded_input"] == ["counties"]


def test_summarizer_expand_noop_without_hints():
    from chunkshop.chunkers._summarizer import build_summarizer
    from chunkshop.config import CallableSummarizer
    cfg = CallableSummarizer(
        mode="callable",
        module="tests.chunkshop.test_hint_expansion",
        function="_echo",
        kwargs={},                      # no hints anywhere
        expand={"kinds": ["lemma"]},
    )
    fn = build_summarizer(cfg)
    fn("body", {})
    assert "hints" not in _ECHO["kwargs"]


_ECHO = {}


def _echo(text, **kwargs):
    _ECHO.clear()
    _ECHO["kwargs"] = dict(kwargs)
    _ECHO["expanded_input"] = kwargs.get("hints")
    return text
```

To make `test_summarizer_expand_runs_on_resolved_hints` deterministic without a spaCy model, the dispatch must call `chunkshop.hints.expand_hints`, which the test monkeypatches:

```python
@pytest.fixture(autouse=True)
def _stub_expand(monkeypatch):
    import chunkshop.chunkers._summarizer as M
    monkeypatch.setattr(M, "expand_hints", lambda h, **k: h, raising=False)
    import chunkshop.extractors.lede_top_terms as E
    monkeypatch.setattr(E, "expand_hints", lambda h, **k: h, raising=False)
    yield
```

(Place the fixture near the top of the test module.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/chunkshop/test_hint_expansion.py -v`
Expected: FAIL (`expand` not a field; dispatch doesn't expand).

- [ ] **Step 3: Add `expand` fields to both config models**

In `config.py`, add to `CallableSummarizer`:

```python
    expand: Optional["HintExpansion"] = None
```

and to `LedeTopTermsExtractor`:

```python
    expand: Optional["HintExpansion"] = None
```

(`HintExpansion` is defined above both, so no `model_rebuild` is needed; if a forward-ref error appears, call `CallableSummarizer.model_rebuild()` after `HintExpansion` is defined.)

- [ ] **Step 4: Wire expansion into the summarizer dispatch**

In `_summarizer.py`, add a module-level import at the top:

```python
from chunkshop.hints import expand_hints
```

> This is allowed: `chunkshop.hints` lazy-imports lede_spacy *inside* its function, so importing `chunkshop.hints` does not import lede_spacy. The SC-016 grep matches `import lede`/`import lede_spacy`, not `from chunkshop.hints import ...`.

Then, in `_callable`, after the hints are resolved and before `return fn(...)`, insert:

```python
            if cfg.expand is not None and call_kwargs.get("hints"):
                call_kwargs["hints"] = expand_hints(
                    call_kwargs["hints"],
                    kinds=tuple(cfg.expand.kinds),
                    top_k=cfg.expand.top_k,
                    expand_weight=cfg.expand.expand_weight,
                )
```

- [ ] **Step 5: Wire expansion into the extractor**

In `extractors/lede_top_terms.py`, add at top:

```python
from chunkshop.hints import expand_hints
```

In `extract()`, before the `top_terms(...)` call, compute the effective hints:

```python
        hints = self.cfg.hints
        if self.cfg.expand is not None and hints:
            hints = expand_hints(
                hints,
                kinds=tuple(self.cfg.expand.kinds),
                top_k=self.cfg.expand.top_k,
                expand_weight=self.cfg.expand.expand_weight,
            )
```

and pass `hints=hints` (instead of `hints=self.cfg.hints`) to `top_terms(...)`.

- [ ] **Step 6: Run to verify L4 wiring tests pass**

Run: `uv run pytest tests/chunkshop/test_hint_expansion.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add python/src/chunkshop/config.py python/src/chunkshop/chunkers/_summarizer.py python/src/chunkshop/extractors/lede_top_terms.py python/tests/chunkshop/test_hint_expansion.py
git commit -m "feat(hints): wire expand: block into summarizer + top_terms extractor (SC-020, SC-021, SC-022)"
```

---

### Task 14: Expansion docs + final boundary re-check (SC-023, SC-016)

**Files:**
- Modify: `docs/quickstart-summaries.md`
- Test: `python/tests/chunkshop/test_no_lede_core_imports.py` (already allowlists `hints.py` — just re-run)

- [ ] **Step 1: Add the "Hint expansion (lede-spacy)" subsection (SC-023)**

Append to `docs/quickstart-summaries.md`:

```markdown
### Hint expansion (lede-spacy)

Install the optional extra: `pip install 'lede-spacy>=0.4'`. Add an `expand:`
block under the summarizer (or the `lede_top_terms` extractor) to widen hints
before lede ranks:

    summarizer:
      mode: callable
      module: chunkshop.summarizers.lede
      kwargs: {hints: [counties], hint_focus: 0.7}
      expand:
        kinds: [lemma]          # lemma | synonyms | similar
        top_k: 5
        expand_weight: 0.5

Expansion kinds and their requirements:
- `lemma` — any spaCy model (e.g. `python -m spacy download en_core_web_sm`).
  ~2x your hint count. "counties" -> +"county".
- `synonyms` — `pip install 'lede-spacy[synonyms]'` + `python -m nltk.downloader
  wordnet`. ~7x at top_k=3. Single-word hints only.
- `similar` — a vector model (`en_core_web_md`/`_lg`). ~15x+ at top_k=5.

These corpora/models are NOT pulled by chunkshop; a missing one raises an
actionable error naming the install command. Expansion is Python-only.
```

- [ ] **Step 2: Final import-boundary re-check (SC-016)**

Run: `uv run pytest tests/chunkshop/test_no_lede_core_imports.py -v`
Expected: PASS (`hints.py` is allowlisted; `_summarizer.py` and the extractor import `from chunkshop.hints import ...`, not lede directly).

- [ ] **Step 3: Commit**

```bash
git add docs/quickstart-summaries.md
git commit -m "docs(summaries): document lede-spacy hint expansion (SC-023)"
```

---

### ⛔ DC-003 — Gate after L4, before final

- [ ] **Re-read** the mission brief.
- [ ] Verify SC-017..SC-023 evidence (extra resolves; `hints.py` shim + wrapped errors; `HintExpansion`; both `expand` wirings; L4 tests; docs).
- [ ] **Re-run SC-015 and SC-016 a third time** — L4 touched `_summarizer.py`, `config.py`, the extractor, and added `hints.py`:
```bash
cd python && uv run pytest tests/chunkshop/test_lede_no_hints_byte_identical.py tests/chunkshop/test_no_lede_core_imports.py tests/chunkshop/test_hint_expansion.py -v
```
Expected: all PASS.
- [ ] Drift check (Purpose / SC mapping / Out of Scope).

---

### ⛔ DC-FINAL — Before marking complete

- [ ] **Re-read** the mission brief one final time.
- [ ] For each SC-001..SC-023, point to the file/test/command proving satisfaction (build a checklist in the final summary).
- [ ] Full suite:
```bash
cd python && uv run pytest -q
```
Expected: PASS modulo the documented DSN-gated and spaCy-model-gated skips.
- [ ] Confirm Constraints' ALWAYS items hold; no NEVER item violated (no forced nltk/WordNet/vector-model deps; no `[nlp]` pin edit; shim stays pure passthrough; Rust untouched; existing sample YAMLs untouched).
- [ ] Confirm no Out-of-Scope item crept in (no read-side/search surface; no Sumy change; no bakeoff config change).
- [ ] Produce the end-of-work summary (CHANGES MADE / DIDN'T TOUCH / POTENTIAL CONCERNS).

---

## Self-Review

- **Spec coverage:** SC-001→T1; SC-002→T5; SC-003→T6; SC-004→T6; SC-005→T2; SC-006→T7; SC-007/008/009→T8; SC-010→T10; SC-011→T9; SC-012→T9/T10; SC-013→T10; SC-014→T10; SC-015→T3; SC-016→T4/T14; SC-017→T1; SC-018→T12; SC-019→T11; SC-020→T13; SC-021→T13; SC-022→T13(+T12); SC-023→T14. All 23 covered. DC-001→after T6; DC-002→after T8; DC-003→after T14; DC-FINAL→end.
- **Placeholder scan:** `GOLDEN = "PASTE_GOLDEN_HERE"` is an intentional capture step (Task 3 Step 1 produces the value); `_unpack` carries an explicit "confirm exact lede shape" instruction. No silent placeholders.
- **Type consistency:** `expand_hints(hints, *, kinds, top_k, expand_weight)` signature identical in `hints.py`, summarizer dispatch, and extractor. `HintExpansion` field names (`kinds`/`top_k`/`expand_weight`) consistent. `ExtractResult.tags` used as `list[str]` throughout (matches the dataclass). `top_terms` entry dict keys `term`/`score`/`kind` consistent between extractor and tests.
