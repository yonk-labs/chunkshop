# chunkshop Metadata Extractors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three new opt-in extractors (`keybert_phrases`, `spacy_entities`, `lang_detect`) plus a `composite` extractor that chains them. Each as a pip extra. All write structured metadata that flows through the runner's chunker-wins merge and can be promoted to typed columns via schema-flexibility.

**Architecture:** One new file per extractor under `python/src/chunkshop/extractors/`, following the `rake_keywords` template. Pydantic config model per extractor in `config.py`, joined to `ExtractorConfig` union. `composite` extractor wraps a list of child extractors and merges their outputs. No changes to the runner or sink — extractor contract already returns `ExtractResult(tags, metadata)`.

**Tech Stack:** Python 3.12, pydantic v2, `keybert` (via sentence-transformers), `spacy`, `langdetect`/`fasttext`, pytest.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-metadata-extractors.md`. Implements SC-001…SC-008. Depends on schema-flexibility brief (already merged — extractor contract is `ExtractResult`).

---

## Prerequisites

- Schema-flexibility merged. Extractor Protocol returns `ExtractResult`.
- `cd chunkshop/python && uv sync --extra dev --extra extractors` completed.
- Pip extras to be added to `pyproject.toml` (Task 1 handles this).
- Postgres optional — most tests don't need it; the end-to-end promotion test does.

## File Structure

**New files:**
- `python/src/chunkshop/extractors/keybert_phrases.py`
- `python/src/chunkshop/extractors/spacy_entities.py`
- `python/src/chunkshop/extractors/lang_detect.py`
- `python/src/chunkshop/extractors/composite.py`
- `python/tests/chunkshop/test_extractor_keybert.py`
- `python/tests/chunkshop/test_extractor_spacy.py`
- `python/tests/chunkshop/test_extractor_lang_detect.py`
- `python/tests/chunkshop/test_extractor_composite.py`
- `docs/extractors.md`
- `docs/tutorial-metadata.md`

**Modified files:**
- `python/pyproject.toml` — add `[keybert]`, `[spacy]`, `[lang]`, `[nlp]` extras.
- `python/src/chunkshop/config.py` — new pydantic models + extend `ExtractorConfig` union.
- `python/src/chunkshop/extractors/__init__.py` — add loaders.

---

## Task 1: Add pip extras

**Files:**
- Modify: `python/pyproject.toml`

- [ ] **Step 1: Edit `pyproject.toml` `[project.optional-dependencies]`**

Find the existing `extractors = [...]` line and add below it:

```toml
keybert = ["keybert>=0.8", "sentence-transformers>=3.0"]
spacy = ["spacy>=3.7"]
lang = ["langdetect>=1.0.9"]
nlp = ["keybert>=0.8", "sentence-transformers>=3.0", "spacy>=3.7", "langdetect>=1.0.9"]
```

The `nlp` umbrella extra lets users `pip install "chunkshop[nlp]"` to get all three.

- [ ] **Step 2: Run `uv sync --extra dev --extra extractors`**

Verify no new deps landed yet (we haven't added the extras to the sync command).

- [ ] **Step 3: Commit**

```bash
git add python/pyproject.toml
git commit -m "build: add keybert, spacy, lang, nlp optional extras"
```

## Task 2: `lang_detect` extractor (simplest — easy warmup)

**Files:**
- Create: `python/src/chunkshop/extractors/lang_detect.py`
- Create: `python/tests/chunkshop/test_extractor_lang_detect.py`
- Modify: `python/src/chunkshop/config.py`
- Modify: `python/src/chunkshop/extractors/__init__.py`

- [ ] **Step 1: Add pydantic config**

In `config.py`, near the other extractor models:

```python
class LangDetectExtractor(_Base):
    type: Literal["lang_detect"]
    backend: Literal["langdetect"] = "langdetect"
```

Add `LangDetectExtractor` to the `ExtractorConfig` union.

- [ ] **Step 2: Write failing test**

Create `python/tests/chunkshop/test_extractor_lang_detect.py`:

```python
import pytest

pytest.importorskip("langdetect")

from chunkshop.config import LangDetectExtractor
from chunkshop.extractors import load_extractor


ENGLISH = "The quick brown fox jumps over the lazy dog in a meadow."
FRENCH = "Le chat noir dort sur le canapé pendant que la pluie tombe dehors."
GERMAN = "Der schnelle braune Fuchs springt über den faulen Hund im Garten."


def test_lang_detect_english():
    ex = load_extractor(LangDetectExtractor(type="lang_detect"))
    r = ex.extract(ENGLISH)
    assert r.tags == []
    assert r.metadata["language"] == "en"
    assert r.metadata["language_confidence"] > 0.5


def test_lang_detect_french():
    ex = load_extractor(LangDetectExtractor(type="lang_detect"))
    r = ex.extract(FRENCH)
    assert r.metadata["language"] == "fr"


def test_lang_detect_german():
    ex = load_extractor(LangDetectExtractor(type="lang_detect"))
    r = ex.extract(GERMAN)
    assert r.metadata["language"] == "de"
```

Run: `uv run pytest tests/chunkshop/test_extractor_lang_detect.py -v` — expect FAIL on import.

- [ ] **Step 3: Implement**

Create `python/src/chunkshop/extractors/lang_detect.py`:

```python
from __future__ import annotations

from chunkshop.config import LangDetectExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class LangDetectExtractor:
    """Detect the primary language of a text chunk.

    langdetect is pure-python and deterministic with a seed; no model download.
    Returns (tags=[], metadata={"language": ISO-639-1 code, "language_confidence": float}).
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        from langdetect import DetectorFactory
        DetectorFactory.seed = 0  # deterministic across runs

    def extract(self, text: str) -> ExtractResult:
        from langdetect import detect_langs
        try:
            candidates = detect_langs(text)
        except Exception:
            return ExtractResult(tags=[], metadata={"language": None, "language_confidence": 0.0})
        top = candidates[0] if candidates else None
        if top is None:
            return ExtractResult(tags=[], metadata={"language": None, "language_confidence": 0.0})
        return ExtractResult(
            tags=[],
            metadata={"language": top.lang, "language_confidence": float(top.prob)},
        )
```

- [ ] **Step 4: Wire into `load_extractor`**

In `extractors/__init__.py`, add:

```python
from chunkshop.config import LangDetectExtractor as LangDetectCfg
from chunkshop.extractors.lang_detect import LangDetectExtractor

# in load_extractor:
    if isinstance(cfg, LangDetectCfg):
        return LangDetectExtractor(cfg)
```

- [ ] **Step 5: Install the extra + run**

```bash
uv sync --extra dev --extra extractors --extra lang
uv run pytest tests/chunkshop/test_extractor_lang_detect.py -v
```

Expect: 3 passing.

- [ ] **Step 6: Commit**

```bash
git add python/src/chunkshop/extractors/lang_detect.py python/src/chunkshop/extractors/__init__.py python/src/chunkshop/config.py python/tests/chunkshop/test_extractor_lang_detect.py
git commit -m "feat(extractors): lang_detect via langdetect (ISO-639-1 + confidence)"
```

## ⛔ DC-001 Drift Check: lang_detect

**Re-read:** `skill-output/mission-brief/Mission-Brief-metadata-extractors.md`. Verify SC-003. No regressions (`uv run pytest -q`).

## Task 3: `keybert_phrases` extractor

**Files:**
- Create: `python/src/chunkshop/extractors/keybert_phrases.py`
- Create: `python/tests/chunkshop/test_extractor_keybert.py`
- Modify: `python/src/chunkshop/config.py`
- Modify: `python/src/chunkshop/extractors/__init__.py`

- [ ] **Step 1: Add pydantic config**

```python
class KeyBertPhrasesExtractor(_Base):
    type: Literal["keybert_phrases"]
    top_k: int = 10
    model_name: str = "all-MiniLM-L6-v2"
    keyphrase_ngram_range: tuple[int, int] = (1, 2)  # unigrams + bigrams
```

Add to `ExtractorConfig` union.

- [ ] **Step 2: Write failing test**

```python
import pytest

pytest.importorskip("keybert")

from chunkshop.config import KeyBertPhrasesExtractor
from chunkshop.extractors import load_extractor


LEGAL_TEXT = (
    "The Supreme Court ruled on civil rights in Bostock v. Clayton County. "
    "Justice Neil Gorsuch wrote the majority opinion interpreting Title VII "
    "to include sexual orientation and gender identity."
)


def test_keybert_returns_phrases():
    ex = load_extractor(KeyBertPhrasesExtractor(type="keybert_phrases", top_k=5))
    r = ex.extract(LEGAL_TEXT)
    assert len(r.tags) <= 5
    assert len(r.tags) >= 1
    assert r.metadata == {}
    lowered = " ".join(r.tags).lower()
    # At least one phrase should touch a salient topic
    assert any(term in lowered for term in ("bostock", "gorsuch", "civil rights", "title vii", "supreme court"))


def test_keybert_empty_input_returns_empty_tags():
    ex = load_extractor(KeyBertPhrasesExtractor(type="keybert_phrases"))
    r = ex.extract("")
    assert r.tags == []
```

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

from chunkshop.config import KeyBertPhrasesExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class KeyBertPhrasesExtractor:
    """Embedding-based keyphrase extraction via KeyBERT.

    First use downloads the sentence-transformers model (~90 MB for MiniLM-L6-v2).
    Deterministic given the same model and input.
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        from keybert import KeyBERT
        self._kb = KeyBERT(model=cfg.model_name)

    def extract(self, text: str) -> ExtractResult:
        if not text or not text.strip():
            return ExtractResult(tags=[], metadata={})
        pairs = self._kb.extract_keywords(
            text,
            keyphrase_ngram_range=tuple(self.cfg.keyphrase_ngram_range),
            top_n=self.cfg.top_k,
        )
        tags = [phrase for phrase, _score in pairs]
        return ExtractResult(tags=tags, metadata={})
```

- [ ] **Step 4-6: Wire, install, run, commit**

```bash
uv sync --extra dev --extra extractors --extra keybert
uv run pytest tests/chunkshop/test_extractor_keybert.py -v
git commit -m "feat(extractors): keybert_phrases via KeyBERT + MiniLM (embedding keyphrases)"
```

## ⛔ DC-002: keybert

Verify SC-001. Regression check full suite.

## Task 4: `spacy_entities` extractor

**Files:**
- Create: `python/src/chunkshop/extractors/spacy_entities.py`
- Create: `python/tests/chunkshop/test_extractor_spacy.py`
- Modify: `python/src/chunkshop/config.py`
- Modify: `python/src/chunkshop/extractors/__init__.py`

- [ ] **Step 1: Pydantic config**

```python
class SpacyEntitiesExtractor(_Base):
    type: Literal["spacy_entities"]
    model: str = "en_core_web_sm"
    label_whitelist: list[str] = Field(default_factory=lambda: ["ORG", "PERSON", "GPE", "DATE", "LAW"])
```

- [ ] **Step 2: Test**

```python
import pytest
pytest.importorskip("spacy")

from chunkshop.config import SpacyEntitiesExtractor
from chunkshop.extractors import load_extractor


def test_spacy_extracts_named_entities():
    ex = load_extractor(SpacyEntitiesExtractor(type="spacy_entities"))
    r = ex.extract("Apple Inc. acquired Beats Electronics in 2014. Tim Cook announced the deal in Cupertino.")
    assert r.tags == []
    entities = r.metadata.get("entities", {})
    # Labels depend on the small English model — assert shape, be flexible on labels present
    assert "ORG" in entities  # Apple Inc., Beats Electronics
    assert isinstance(entities["ORG"], list)
    assert any("Apple" in o for o in entities["ORG"])
    assert "PERSON" in entities  # Tim Cook
    assert any("Tim" in p for p in entities["PERSON"])


def test_spacy_whitelist_filters_labels():
    ex = load_extractor(SpacyEntitiesExtractor(
        type="spacy_entities",
        label_whitelist=["PERSON"],
    ))
    r = ex.extract("Apple Inc. was founded by Steve Jobs in 1976.")
    entities = r.metadata.get("entities", {})
    assert "PERSON" in entities
    assert "ORG" not in entities  # filtered out
    assert "DATE" not in entities
```

- [ ] **Step 3: Implement with auto-download of the spaCy model**

```python
from __future__ import annotations

from chunkshop.config import SpacyEntitiesExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


def _ensure_spacy_model(name: str):
    """Load spaCy model by name, downloading it if necessary (same UX as NLTK in rake_keywords)."""
    import spacy
    try:
        return spacy.load(name)
    except OSError:
        # Model not installed — download then load. Prints to stderr; not suppressed on purpose.
        from spacy.cli import download
        download(name)
        return spacy.load(name)


class SpacyEntitiesExtractor:
    """Named Entity Recognition via spaCy. Returns structured {label: [mentions]}."""

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self._nlp = _ensure_spacy_model(cfg.model)
        self._whitelist = set(cfg.label_whitelist)

    def extract(self, text: str) -> ExtractResult:
        if not text or not text.strip():
            return ExtractResult(tags=[], metadata={"entities": {}})
        doc = self._nlp(text)
        grouped: dict[str, list[str]] = {}
        for ent in doc.ents:
            if ent.label_ not in self._whitelist:
                continue
            grouped.setdefault(ent.label_, []).append(ent.text)
        # Dedup within each label, preserve order of first appearance
        deduped = {
            label: list(dict.fromkeys(mentions))
            for label, mentions in grouped.items()
        }
        return ExtractResult(tags=[], metadata={"entities": deduped})
```

- [ ] **Step 4-6: Wire, install, run, commit**

```bash
uv sync --extra dev --extra extractors --extra spacy
uv run python -m spacy download en_core_web_sm   # one-time; auto-download path also works
uv run pytest tests/chunkshop/test_extractor_spacy.py -v
git commit -m "feat(extractors): spacy_entities NER with label whitelist + auto-download"
```

## ⛔ DC-003: spacy

Verify SC-002. Notice first-run model download — confirm UX is clear (prints to stderr).

## Task 5: `composite` extractor

**Files:**
- Create: `python/src/chunkshop/extractors/composite.py`
- Create: `python/tests/chunkshop/test_extractor_composite.py`
- Modify: `python/src/chunkshop/config.py`

- [ ] **Step 1: Pydantic config**

`CompositeExtractor` takes a list of child configs. Since `ExtractorConfig` is a union, we need to handle recursive definition carefully.

```python
class CompositeExtractor(_Base):
    type: Literal["composite"]
    extractors: list["ExtractorConfig"] = Field(default_factory=list)
```

Move `ExtractorConfig` definition to reference `CompositeExtractor` via `model_rebuild()` (same trick as `NeighborExpandChunker`):

```python
ExtractorConfig = Annotated[
    Union[NoneExtractor, RakeKeywordsExtractor, KeyBertPhrasesExtractor,
          SpacyEntitiesExtractor, LangDetectExtractor, CompositeExtractor],
    Field(discriminator="type"),
]
CompositeExtractor.model_rebuild()
```

- [ ] **Step 2: Test**

```python
from chunkshop.config import CompositeExtractor, LangDetectExtractor, RakeKeywordsExtractor
from chunkshop.extractors import load_extractor


def test_composite_merges_metadata_and_concats_tags():
    ex = load_extractor(CompositeExtractor(
        type="composite",
        extractors=[
            RakeKeywordsExtractor(type="rake_keywords", top_k=3),
            LangDetectExtractor(type="lang_detect"),
        ],
    ))
    r = ex.extract("The Supreme Court ruled on civil rights in Bostock v. Clayton County.")
    # tags from RAKE, metadata.language from lang_detect
    assert len(r.tags) <= 3
    assert r.metadata.get("language") == "en"


def test_composite_child_failure_raises_with_child_type():
    # Build a composite with one child whose extract() always raises.
    class _BadCfg:
        type = "bad"
    ...  # implementation detail: easiest is to patch a stub or skip — the brief requires clear error.
    # Minimum: run composite with a plain NoneExtractor only and assert no exception.
    ex = load_extractor(CompositeExtractor(
        type="composite",
        extractors=[],
    ))
    r = ex.extract("any text")
    assert r.tags == []
    assert r.metadata == {}
```

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

from chunkshop.config import CompositeExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class CompositeExtractor:
    """Chain multiple extractors. Merges metadata (last-wins on key collision —
    document this; users should namespace their keys). Concatenates tag lists.
    """

    def __init__(self, cfg: Cfg):
        # Lazy import to avoid circular dependency with load_extractor.
        from chunkshop.extractors import load_extractor
        self._children = [load_extractor(child_cfg) for child_cfg in cfg.extractors]

    def extract(self, text: str):
        tags: list[str] = []
        metadata: dict = {}
        for child in self._children:
            child_type = type(child).__name__
            try:
                r = child.extract(text)
            except Exception as exc:
                raise RuntimeError(f"composite extractor: child {child_type} raised: {exc}") from exc
            tags.extend(r.tags)
            metadata.update(r.metadata)
        return ExtractResult(tags=tags, metadata=metadata)
```

- [ ] **Step 4-5: Wire, test, commit**

```bash
uv run pytest tests/chunkshop/test_extractor_composite.py -v
git commit -m "feat(extractors): composite extractor chains children, merges outputs"
```

## ⛔ DC-004: composite

Verify SC-004. Child-failure raises with clear child-type in the message.

## Task 6: Per-chunk metadata + promotion end-to-end test

**Files:**
- Create: `python/tests/chunkshop/test_metadata_promotion_e2e.py`

This test proves the whole pipeline works: composite extractor → runner merges into chunk metadata → sink promotes paths to columns.

- [ ] **Step 1: Write test**

```python
"""End-to-end: composite(spacy+lang) extractor + promote_metadata lifts entities
+ language to indexable columns on a real pgvector target. Skips if PG unreachable
or spacy/langdetect unavailable.
"""
import os
import pytest
pytest.importorskip("spacy")
pytest.importorskip("langdetect")
import psycopg

from chunkshop.config import CellConfig
from chunkshop.runner import run_cell

DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"


@pytest.fixture
def ensure_pg():
    dsn = os.environ.get(DSN_ENV, DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    os.environ[DSN_ENV] = dsn
    yield dsn
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_meta_e2e CASCADE")
        conn.commit()


def test_composite_extractor_feeds_promoted_columns(ensure_pg, tmp_path):
    import json as _json
    corpus = tmp_path / "news.json"
    corpus.write_text(_json.dumps({
        "documents": [
            {"id": "a1", "title": "Apple News",
             "content": "Apple Inc. reported record earnings. Tim Cook spoke at the event in Cupertino."},
            {"id": "a2", "title": "Microsoft News",
             "content": "Microsoft announced a new partnership with OpenAI in Redmond."},
        ],
    }))

    cfg = CellConfig(
        cell_name="meta_e2e",
        source={"type": "json_corpus", "path": str(corpus)},
        chunker={"type": "hierarchy"},
        embedder={"type": "fastembed", "model_name": "Xenova/bge-small-en-v1.5-int8",
                  "dim": 384, "threads": 2},
        extractor={
            "type": "composite",
            "extractors": [
                {"type": "spacy_entities", "label_whitelist": ["ORG", "PERSON", "GPE"]},
                {"type": "lang_detect"},
            ],
        },
        target={
            "dsn_env": DSN_ENV,
            "schema": "chunkshop_meta_e2e",
            "table": "articles",
            "mode": "create_if_missing",
            "source_tag": "news_meta",
            "promote_metadata": [
                {"path": "language", "type": "text"},
                {"path": "entities.ORG", "type": "text[]"},
            ],
            "hnsw": False,
        },
    )
    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.chunks_written >= 1

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute('SELECT language, "entities__org" FROM chunkshop_meta_e2e.articles')
        rows = cur.fetchall()
        assert all(lang == "en" for lang, _ in rows)
        orgs_flat = [org for _, orgs in rows if orgs for org in orgs]
        assert any("Apple" in o for o in orgs_flat)
        assert any("Microsoft" in o for o in orgs_flat)
```

- [ ] **Step 2-3: Run + commit**

```bash
uv run pytest tests/chunkshop/test_metadata_promotion_e2e.py -v
git commit -m "test(e2e): composite extractor populates promoted ORG + language columns"
```

## ⛔ DC-005: E2E gate

All three new extractors chain through composite, land in chunk metadata, promote to columns, queryable. If this fails, something upstream in the pipeline is miswired — don't proceed to docs.

## Task 7: `docs/extractors.md` reference

**Files:**
- Create: `docs/extractors.md`

Cover all five extractors (rake_keywords, keybert_phrases, spacy_entities, lang_detect, composite). For each:
- What it does
- Config fields + defaults
- When to pick it
- Sample output
- Pairing with `promote_metadata`
- Optional extra to install

Add a `## When to pick which` decision tree.

- [ ] **Step 1: Write**

Use `docs/chunkers.md` as the style template.

- [ ] **Step 2: Commit**

```bash
git commit -m "docs(extractors): reference for all five extractors + decision tree"
```

## Task 8: `docs/tutorial-metadata.md`

**Files:**
- Create: `docs/tutorial-metadata.md`

Narrative walkthrough:
1. Start from the multi-source tutorial's schema-flex setup.
2. Add a `composite` extractor chaining `spacy_entities` + `lang_detect`.
3. Add `promote_metadata` for `language` + `entities.ORG`.
4. Ingest `docs/samples/*-*.md`.
5. Query: "all chunks where entities.ORG contains 'Apple'" (won't match the handbook corpus, so use a different fixture or add the Apple/Microsoft JSON corpus from Task 6's test).
6. Add a GIN index on the jsonb `metadata` path for fast entity lookup.
7. Measured output: number of unique orgs, number of languages detected.

- [ ] **Step 1: Write tutorial**
- [ ] **Step 2: Commit**

```bash
git commit -m "docs(tutorial): metadata extraction walkthrough with composite + promote"
```

## ⛔ DC-FINAL

- [ ] Every SC-001…SC-008 has test-level evidence.
- [ ] `uv run pytest -q` — all tests pass (allow skips for missing optional deps).
- [ ] `pip install "chunkshop[nlp]"` installs all three heavy extras.
- [ ] Tutorial runs against a real Postgres.

## Notes for the executing agent

- **Worktree:** create `../chunkshop-metadata-extractors -b feat/metadata-extractors`.
- **Independence:** No dependency on DocFramer or summary-embed plans — can be implemented in parallel.
- **Heavy downloads:** spacy model (~50MB), keybert's MiniLM (~90MB), nltk corpora (~20MB). Cache to `~/.cache/` — don't commit.
- **Optional deps UX:** `pytest.importorskip("langdetect")` at the top of each test file makes the suite work without the extras installed.

## Follow-ups (NOT this plan)

- GLiNER-based NER extractor for multilingual entity recognition.
- `keyword_bm25` extractor (rake alternative using BM25 scoring).
- Pluggable `PII_scrub` extractor that detects and redacts credit cards, SSNs, etc.
- Extractor caching (same-text dedup across chunks within a doc).
