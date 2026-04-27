# chunkshop Summary-Embed + Hierarchical-Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two chunker wrappers. `summary_embed` replaces each chunk's `embedded_content` with a summary (keeping raw in `original_content`). `hierarchical_summary` emits base rows plus coarse summary rows tagged by granularity. Both consume an origin-agnostic summarizer contract — external source-column, callable (skimr, sumy via shim, any user-wired module), or passthrough baseline. Ship minimal `chunkshop.summarizers` adapter shims for skimr + sumy.

**Architecture:** Two new wrapper chunker implementations under `python/src/chunkshop/chunkers/`. Summarizer config is a discriminated union (`external | callable | passthrough`). Callable mode imports user-specified modules lazily at first chunker use. `chunkshop.summarizers/` module ships as optional extras; its shims expose the canonical `summarize(text, **kwargs) -> str` contract for libraries with incompatible native APIs (like sumy).

**Tech Stack:** Python 3.12, pydantic v2, `skimr` (sibling repo, zero-dep), `sumy` (optional extra), pytest.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-summary-embed.md`. Implements SC-001…SC-010 + SC-005b (shim). Depends on schema-flexibility (already merged — uses `metadata.granularity` promotable via `promote_metadata`).

---

## Prerequisites

- Schema-flexibility merged. Extractor contract returns `ExtractResult`. `promote_metadata` supports arbitrary jsonb paths.
- `cd chunkshop/python && uv sync --extra dev --extra extractors` completed.
- Sibling `skimr` repo at `/home/yonk/yonk-tools/extractive_summary` accessible (path dependency for tests).
- `sumy` as pip extra — lazy-installed only if user opts in.

## File Structure

**New files:**
- `python/src/chunkshop/chunkers/summary_embed.py`
- `python/src/chunkshop/chunkers/hierarchical_summary.py`
- `python/src/chunkshop/chunkers/_summarizer.py` — summarizer config dispatch + callable loader
- `python/src/chunkshop/summarizers/__init__.py`
- `python/src/chunkshop/summarizers/skimr.py` — thin re-export
- `python/src/chunkshop/summarizers/sumy.py` — adapter wrapping sumy's parser+summarizer
- `python/tests/chunkshop/test_chunker_summary_embed.py`
- `python/tests/chunkshop/test_chunker_hierarchical_summary.py`
- `python/tests/chunkshop/test_summarizer_shims.py`
- `docs/summaries.md`
- `docs/tutorial-summaries.md`
- `docs/samples/sample-summary-embed.yaml`
- `docs/samples/sample-hierarchical.yaml`

**Modified files:**
- `python/pyproject.toml` — add `[skimr]`, `[sumy]` optional extras.
- `python/src/chunkshop/config.py` — summarizer config models + `SummaryEmbedChunker` + `HierarchicalSummaryChunker`.
- `python/src/chunkshop/chunkers/__init__.py` — `load_chunker` dispatch.

---

## Task 1: Pip extras + skimr path dependency

**Files:**
- Modify: `python/pyproject.toml`

- [ ] **Step 1: Add extras**

```toml
skimr = []  # installed via uv sources path dep below
sumy = ["sumy>=0.11"]
summarize = []  # umbrella — see [tool.uv.sources] + sumy
```

And under `[tool.uv.sources]`:

```toml
[tool.uv.sources]
skimr = { path = "../../extractive_summary", editable = true }
```

Note: skimr is the sibling repo's package name. Adjust if the path or package name differs.

- [ ] **Step 2: Sync + verify skimr imports**

```bash
uv sync --extra dev --extra extractors --extra sumy
uv run python -c "import skimr; print(skimr.summarize('The quick brown fox jumps over the lazy dog.'))"
```

If the `skimr` import fails, the path dep is misconfigured — investigate before proceeding.

- [ ] **Step 3: Commit**

```bash
git commit -m "build: add skimr path dep + sumy optional extra"
```

## Task 2: Summarizer config models

**Files:**
- Modify: `python/src/chunkshop/config.py`

- [ ] **Step 1: Write failing config tests**

Create `python/tests/chunkshop/test_config_summarizer.py`:

```python
import pytest
from pydantic import ValidationError

from chunkshop.config import (
    ExternalSummarizer, CallableSummarizer, PassthroughSummarizer,
    SummaryEmbedChunker,
)


def test_external_summarizer_requires_field():
    s = ExternalSummarizer(mode="external", field="summary")
    assert s.field == "summary"


def test_callable_summarizer_requires_module_function():
    s = CallableSummarizer(
        mode="callable", module="skimr", function="summarize",
        kwargs={"max_length": 200},
    )
    assert s.module == "skimr"
    assert s.function == "summarize"
    assert s.kwargs["max_length"] == 200


def test_passthrough_summarizer_has_no_fields():
    s = PassthroughSummarizer(mode="passthrough")
    assert s.mode == "passthrough"


def test_summary_embed_chunker_discriminates_summarizer():
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base={"type": "hierarchy"},
        summarizer={"mode": "passthrough"},
    )
    assert cfg.summarizer.mode == "passthrough"
```

- [ ] **Step 2: Implement models in `config.py`**

Near the chunker models:

```python
class ExternalSummarizer(_Base):
    mode: Literal["external"]
    field: str = "summary"


class CallableSummarizer(_Base):
    mode: Literal["callable"]
    module: str
    function: str = "summarize"
    kwargs: dict = Field(default_factory=dict)


class PassthroughSummarizer(_Base):
    mode: Literal["passthrough"]


SummarizerConfig = Annotated[
    Union[ExternalSummarizer, CallableSummarizer, PassthroughSummarizer],
    Field(discriminator="mode"),
]


class SummaryEmbedChunker(_Base):
    type: Literal["summary_embed"]
    base: "ChunkerConfig"
    summarizer: SummarizerConfig


class FixedNGrouping(_Base):
    strategy: Literal["fixed_n"] = "fixed_n"
    n: int = Field(default=5, ge=1)


class WordBudgetGrouping(_Base):
    strategy: Literal["word_budget"] = "word_budget"
    max_words: int = Field(default=2000, ge=100)


class SectionAwareGrouping(_Base):
    strategy: Literal["section_aware"] = "section_aware"


GroupingConfig = Annotated[
    Union[FixedNGrouping, WordBudgetGrouping, SectionAwareGrouping],
    Field(discriminator="strategy"),
]


class HierarchicalSummaryChunker(_Base):
    type: Literal["hierarchical_summary"]
    base: "ChunkerConfig"
    summarizer: SummarizerConfig
    grouping: GroupingConfig = Field(default_factory=lambda: FixedNGrouping())
```

Add `SummaryEmbedChunker` and `HierarchicalSummaryChunker` to the `ChunkerConfig` union. Call `.model_rebuild()` on both (same forward-reference trick as `NeighborExpandChunker`).

- [ ] **Step 3: Run — expect PASS + full-suite regression**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(config): summary-embed + hierarchical-summary chunkers + summarizer modes"
```

## Task 3: Summarizer dispatch helper

**Files:**
- Create: `python/src/chunkshop/chunkers/_summarizer.py`

A tiny module that turns a `SummarizerConfig` into a callable `summarize(text, doc_metadata) -> str`. Keeps the wrapper chunkers clean.

- [ ] **Step 1: Implement**

```python
from __future__ import annotations
from importlib import import_module
from typing import Callable

from chunkshop.config import (
    CallableSummarizer, ExternalSummarizer, PassthroughSummarizer,
)


def build_summarizer(cfg) -> Callable[[str, dict], str]:
    """Return a callable: (chunk_text, doc_metadata) -> summary_string.

    Semantics:
      - external: pull from doc_metadata[cfg.field]; raises if missing.
      - callable: import module.function lazily; invoke as fn(chunk_text, **cfg.kwargs).
      - passthrough: return chunk_text unchanged (baseline).
    """
    if isinstance(cfg, PassthroughSummarizer):
        return lambda text, meta: text

    if isinstance(cfg, ExternalSummarizer):
        field = cfg.field

        def _external(text: str, meta: dict) -> str:
            if field not in meta:
                raise RuntimeError(
                    f"external summarizer: doc.metadata has no field {field!r}. "
                    f"Available keys: {sorted(meta.keys())}"
                )
            value = meta[field]
            if not isinstance(value, str):
                raise RuntimeError(
                    f"external summarizer: doc.metadata[{field!r}] must be a string, "
                    f"got {type(value).__name__}"
                )
            return value

        return _external

    if isinstance(cfg, CallableSummarizer):
        try:
            mod = import_module(cfg.module)
        except ImportError as exc:
            raise RuntimeError(
                f"callable summarizer: could not import {cfg.module!r}: {exc}. "
                f"Install it and retry."
            ) from exc
        fn = getattr(mod, cfg.function, None)
        if fn is None:
            raise RuntimeError(
                f"callable summarizer: module {cfg.module!r} has no attribute {cfg.function!r}"
            )
        kwargs = dict(cfg.kwargs)

        def _callable(text: str, meta: dict) -> str:
            return fn(text, **kwargs)

        return _callable

    raise ValueError(f"unknown summarizer config: {type(cfg).__name__}")
```

- [ ] **Step 2: Unit test**

Create `python/tests/chunkshop/test_summarizer_dispatch.py`:

```python
import pytest
from chunkshop.config import (
    ExternalSummarizer, CallableSummarizer, PassthroughSummarizer,
)
from chunkshop.chunkers._summarizer import build_summarizer


def test_passthrough_returns_text():
    fn = build_summarizer(PassthroughSummarizer(mode="passthrough"))
    assert fn("hello world", {}) == "hello world"


def test_external_pulls_from_metadata():
    fn = build_summarizer(ExternalSummarizer(mode="external", field="abstract"))
    assert fn("raw body", {"abstract": "a short summary"}) == "a short summary"


def test_external_raises_on_missing_field():
    fn = build_summarizer(ExternalSummarizer(mode="external", field="summary"))
    with pytest.raises(RuntimeError, match="no field 'summary'"):
        fn("x", {"other": "value"})


def test_callable_imports_and_invokes():
    # Use `json.dumps` as a stand-in: signature is (obj, **kwargs) -> str.
    # kwargs.indent forces a non-trivial return.
    fn = build_summarizer(CallableSummarizer(
        mode="callable", module="json", function="dumps",
        kwargs={"indent": 2},
    ))
    # json.dumps takes obj not string — but strings are valid objs. OK for test shape.
    result = fn("hello", {})
    assert result == '"hello"'


def test_callable_import_failure_raises_clearly():
    fn_cfg = CallableSummarizer(mode="callable", module="nonexistent_pkg_xyz", function="summarize")
    with pytest.raises(RuntimeError, match="could not import"):
        build_summarizer(fn_cfg)
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/chunkshop/test_summarizer_dispatch.py -v
git commit -m "feat(chunkers): summarizer dispatch helper — external/callable/passthrough"
```

## Task 4: Summarizer shim module (skimr + sumy)

**Files:**
- Create: `python/src/chunkshop/summarizers/__init__.py`
- Create: `python/src/chunkshop/summarizers/skimr.py`
- Create: `python/src/chunkshop/summarizers/sumy.py`
- Create: `python/tests/chunkshop/test_summarizer_shims.py`

The brief's SC-005b: ship thin adapter shims for libraries whose native API doesn't match `summarize(text, **kwargs) -> str`.

- [ ] **Step 1: Shims**

`python/src/chunkshop/summarizers/__init__.py`:

```python
"""Origin-agnostic summarizer shims.

Each sub-module exposes ``summarize(text: str, **kwargs) -> str`` so a user YAML
can reference them uniformly via ``module: chunkshop.summarizers.<name>``.
"""
```

`python/src/chunkshop/summarizers/skimr.py`:

```python
"""Re-export skimr.summarize. No adapter needed — skimr's native signature matches."""
from skimr import summarize  # noqa: F401

__all__ = ["summarize"]
```

`python/src/chunkshop/summarizers/sumy.py`:

```python
"""Adapter wrapping sumy's parser+summarizer+sentence-list into summarize(text, **kwargs) -> str.

sumy's native API is:
    parser = PlaintextParser.from_string(text, Tokenizer(language))
    summarizer = LexRankSummarizer()  # or TextRankSummarizer, LsaSummarizer, LuhnSummarizer, etc.
    summary = summarizer(parser.document, sentences_count=3)
    # summary is a list of sentence objects; join str(sentence) for text.

We expose a single callable that hides all of that.
"""
from __future__ import annotations


_ALGORITHM_IMPORTS = {
    "lex_rank": "sumy.summarizers.lex_rank.LexRankSummarizer",
    "text_rank": "sumy.summarizers.text_rank.TextRankSummarizer",
    "lsa": "sumy.summarizers.lsa.LsaSummarizer",
    "luhn": "sumy.summarizers.luhn.LuhnSummarizer",
    "kl": "sumy.summarizers.kl.KLSummarizer",
    "edmundson": "sumy.summarizers.edmundson.EdmundsonSummarizer",
}


def _load_algorithm(name: str):
    if name not in _ALGORITHM_IMPORTS:
        raise ValueError(
            f"unknown sumy algorithm {name!r}; choose one of {sorted(_ALGORITHM_IMPORTS)}"
        )
    module_path, cls_name = _ALGORITHM_IMPORTS[name].rsplit(".", 1)
    from importlib import import_module
    return getattr(import_module(module_path), cls_name)


def summarize(
    text: str,
    *,
    algorithm: str = "lex_rank",
    sentences_count: int = 3,
    language: str = "english",
) -> str:
    """Extractive summarization via sumy's pluggable algorithms.

    Args:
        text: Input text to summarize.
        algorithm: One of 'lex_rank' (default), 'text_rank', 'lsa', 'luhn', 'kl', 'edmundson'.
        sentences_count: Number of sentences to keep in the summary.
        language: NLTK language code ('english', 'french', etc.).

    Returns:
        Space-joined summary string.
    """
    if not text or not text.strip():
        return ""
    from sumy.parsers.plaintext import PlaintextParser
    from sumy.nlp.tokenizers import Tokenizer
    algo_cls = _load_algorithm(algorithm)
    parser = PlaintextParser.from_string(text, Tokenizer(language))
    summarizer = algo_cls()
    sentences = summarizer(parser.document, sentences_count=sentences_count)
    return " ".join(str(s) for s in sentences)
```

- [ ] **Step 2: Tests**

```python
import pytest
import importlib.util


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


SAMPLE = (
    "The Roman Empire spanned three continents at its height. It collapsed in the west in 476 CE. "
    "Eastern half survived as the Byzantine Empire for another thousand years. Its capital "
    "Constantinople was a center of learning and trade. The fall of Constantinople in 1453 "
    "marked the end of the medieval era."
)


@pytest.mark.skipif(not _has("skimr"), reason="skimr not installed")
def test_skimr_shim_returns_string():
    from chunkshop.summarizers.skimr import summarize
    s = summarize(SAMPLE, max_length=200)
    assert isinstance(s, str)
    assert s
    assert len(s) < len(SAMPLE)


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_lex_rank():
    from chunkshop.summarizers.sumy import summarize
    s = summarize(SAMPLE, algorithm="lex_rank", sentences_count=2)
    assert isinstance(s, str)
    assert s
    # Two sentences worth of text, should be smaller than input
    assert len(s) < len(SAMPLE)


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_text_rank():
    from chunkshop.summarizers.sumy import summarize
    s = summarize(SAMPLE, algorithm="text_rank", sentences_count=2)
    assert s


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_unknown_algorithm_raises():
    from chunkshop.summarizers.sumy import summarize
    with pytest.raises(ValueError, match="unknown sumy algorithm"):
        summarize(SAMPLE, algorithm="nope")


@pytest.mark.skipif(not _has("sumy"), reason="sumy not installed")
def test_sumy_shim_empty_input_returns_empty():
    from chunkshop.summarizers.sumy import summarize
    assert summarize("") == ""
```

- [ ] **Step 3: Install + run + commit**

```bash
uv sync --extra dev --extra extractors --extra sumy
uv run pytest tests/chunkshop/test_summarizer_shims.py -v
git commit -m "feat(summarizers): skimr re-export + sumy adapter shim"
```

## ⛔ DC-001: Summarizer infrastructure

Verify SC-002 + SC-005 + SC-005b. All shims green or skipped cleanly.

## Task 5: `SummaryEmbedChunker` wrapper

**Files:**
- Create: `python/src/chunkshop/chunkers/summary_embed.py`
- Create: `python/tests/chunkshop/test_chunker_summary_embed.py`
- Modify: `python/src/chunkshop/chunkers/__init__.py`

- [ ] **Step 1: Write tests**

```python
from chunkshop.config import SummaryEmbedChunker, SentenceAwareChunker
from chunkshop.chunkers import load_chunker
from chunkshop.sources.base import Document


TEXT = "# Alpha\n\nAlpha bravo charlie. Delta echo foxtrot.\n\n# Golf\n\nGolf hotel india."


def test_summary_embed_passthrough():
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base=SentenceAwareChunker(),
        summarizer={"mode": "passthrough"},
    )
    chunker = load_chunker(cfg)
    chunks = chunker.chunk(Document(id="d1", content=TEXT, title="t", metadata={}))
    assert len(chunks) >= 1
    for c in chunks:
        # Passthrough: summary == original
        assert c.original_content == c.embedded_content
        assert c.metadata.get("summarizer") == "passthrough"


def test_summary_embed_external():
    cfg = SummaryEmbedChunker(
        type="summary_embed",
        base=SentenceAwareChunker(),
        summarizer={"mode": "external", "field": "summary"},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT, title="t",
                   metadata={"summary": "pre-computed one-line summary"})
    chunks = chunker.chunk(doc)
    for c in chunks:
        assert c.embedded_content == "pre-computed one-line summary"
        # Original content is the raw chunk (from the base chunker)
        assert "bravo" in c.original_content or "hotel" in c.original_content
        assert c.metadata.get("summarizer") == "external"


def test_summary_embed_callable_with_fake_module(tmp_path, monkeypatch):
    # Write a fake summarizer module and put it on sys.path.
    import sys
    (tmp_path / "fake_summer.py").write_text(
        "def summarize(text, **kwargs):\n"
        "    return f'SUM[{len(text)}]'\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        cfg = SummaryEmbedChunker(
            type="summary_embed",
            base=SentenceAwareChunker(),
            summarizer={"mode": "callable", "module": "fake_summer", "function": "summarize"},
        )
        chunker = load_chunker(cfg)
        chunks = chunker.chunk(Document(id="d1", content=TEXT, title="t", metadata={}))
        for c in chunks:
            assert c.embedded_content.startswith("SUM[")
            assert c.metadata.get("summarizer") == "callable"
    finally:
        sys.modules.pop("fake_summer", None)
```

- [ ] **Step 2: Implement**

```python
from __future__ import annotations
from dataclasses import replace

from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers._summarizer import build_summarizer
from chunkshop.config import SummaryEmbedChunker as Cfg
from chunkshop.sources.base import Document


class SummaryEmbedChunker:
    """Wrap any base chunker and replace each chunk's embedded_content with a summary.

    original_content stays as the raw base-chunker output. metadata.summarizer
    is stamped with the chosen mode (external/callable/passthrough) for traceability.
    """

    def __init__(self, cfg: Cfg, base: Chunker):
        self.cfg = cfg
        self.base = base
        self._summarize = build_summarizer(cfg.summarizer)
        self._mode = cfg.summarizer.mode

    def chunk(self, doc: Document) -> list[Chunk]:
        base_chunks = self.base.chunk(doc)
        out: list[Chunk] = []
        for bc in base_chunks:
            summary = self._summarize(bc.original_content, dict(doc.metadata or {}))
            meta = {**bc.metadata, "summarizer": self._mode}
            out.append(replace(bc, embedded_content=summary, metadata=meta))
        return out
```

- [ ] **Step 3: Wire `load_chunker`**

In `chunkers/__init__.py`:

```python
from chunkshop.chunkers.summary_embed import SummaryEmbedChunker
from chunkshop.config import SummaryEmbedChunker as SummaryEmbedCfg

# in load_chunker:
    if isinstance(cfg, SummaryEmbedCfg):
        base = load_chunker(cfg.base, **kwargs)
        return SummaryEmbedChunker(cfg, base)
```

- [ ] **Step 4: Run + commit**

```bash
uv run pytest tests/chunkshop/test_chunker_summary_embed.py -v
git commit -m "feat(chunkers): SummaryEmbedChunker wraps base chunker; replaces embedded_content"
```

## ⛔ DC-002: SummaryEmbed

Verify SC-001 + SC-002 all three modes.

## Task 6: `HierarchicalSummaryChunker`

**Files:**
- Create: `python/src/chunkshop/chunkers/hierarchical_summary.py`
- Create: `python/tests/chunkshop/test_chunker_hierarchical_summary.py`
- Modify: `python/src/chunkshop/chunkers/__init__.py`

- [ ] **Step 1: Tests**

```python
from chunkshop.config import (
    HierarchicalSummaryChunker, HierarchyChunker, SentenceAwareChunker,
)
from chunkshop.chunkers import load_chunker
from chunkshop.sources.base import Document


TEXT_WITH_HEADINGS = (
    "# Alpha\n\n" + ("Alpha body sentence. " * 20) +
    "\n\n# Bravo\n\n" + ("Bravo body sentence. " * 20) +
    "\n\n# Charlie\n\n" + ("Charlie body sentence. " * 20)
)


def test_hierarchical_fixed_n_emits_fine_plus_coarse():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=SentenceAwareChunker(),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "fixed_n", "n": 2},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)

    fine = [c for c in chunks if c.metadata.get("granularity") == "fine"]
    coarse = [c for c in chunks if c.metadata.get("granularity") == "coarse"]
    assert len(fine) > 0
    assert len(coarse) > 0
    # Each group_id should appear on one coarse + its fine members
    fine_groups = {c.metadata["group_id"] for c in fine}
    coarse_groups = {c.metadata["group_id"] for c in coarse}
    assert coarse_groups.issubset(fine_groups)


def test_hierarchical_section_aware_requires_hierarchy_base():
    with pytest.raises(ValueError, match="section_aware"):
        HierarchicalSummaryChunker(
            type="hierarchical_summary",
            base=SentenceAwareChunker(),  # not hierarchy
            summarizer={"mode": "passthrough"},
            grouping={"strategy": "section_aware"},
        )


def test_hierarchical_section_aware_with_hierarchy_base():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=HierarchyChunker(),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "section_aware"},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)
    fine = [c for c in chunks if c.metadata.get("granularity") == "fine"]
    coarse = [c for c in chunks if c.metadata.get("granularity") == "coarse"]
    # section_aware: one coarse row per heading section
    assert len(coarse) == 3  # Alpha, Bravo, Charlie
    assert len(fine) == 3


def test_hierarchical_word_budget():
    cfg = HierarchicalSummaryChunker(
        type="hierarchical_summary",
        base=SentenceAwareChunker(),
        summarizer={"mode": "passthrough"},
        grouping={"strategy": "word_budget", "max_words": 50},
    )
    chunker = load_chunker(cfg)
    doc = Document(id="d1", content=TEXT_WITH_HEADINGS, title="t", metadata={})
    chunks = chunker.chunk(doc)
    coarse = [c for c in chunks if c.metadata.get("granularity") == "coarse"]
    assert len(coarse) >= 2  # budget should force multiple groups
```

- [ ] **Step 2: Validate section_aware config-time**

In `config.py`, add a `model_validator` on `HierarchicalSummaryChunker`:

```python
@model_validator(mode="after")
def _section_aware_requires_hierarchy_base(self):
    if getattr(self.grouping, "strategy", None) == "section_aware":
        base_type = getattr(self.base, "type", None)
        if base_type != "hierarchy":
            raise ValueError(
                f"hierarchical_summary with strategy='section_aware' requires "
                f"base.type='hierarchy', got {base_type!r}"
            )
    return self
```

- [ ] **Step 3: Implement**

```python
from __future__ import annotations
from dataclasses import replace
import uuid

from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers._summarizer import build_summarizer
from chunkshop.config import (
    HierarchicalSummaryChunker as Cfg,
    FixedNGrouping, WordBudgetGrouping, SectionAwareGrouping,
)
from chunkshop.sources.base import Document


class HierarchicalSummaryChunker:
    """Emit base (fine) chunks plus coarse summary chunks linked by group_id.

    Grouping strategies:
      - fixed_n:       N consecutive base chunks per group.
      - word_budget:   accumulate chunks up to M words per group.
      - section_aware: one group per original heading (requires base=hierarchy).
    """

    def __init__(self, cfg: Cfg, base: Chunker):
        self.cfg = cfg
        self.base = base
        self._summarize = build_summarizer(cfg.summarizer)
        self._mode = cfg.summarizer.mode

    def chunk(self, doc: Document) -> list[Chunk]:
        base_chunks = self.base.chunk(doc)
        if not base_chunks:
            return []
        groups = self._group(base_chunks)

        out: list[Chunk] = []
        seq = 0
        for group_idx, group_chunks in enumerate(groups):
            group_id = f"{doc.id}::g{group_idx}"
            # Emit fine rows
            for bc in group_chunks:
                meta = {
                    **bc.metadata,
                    "granularity": "fine",
                    "group_id": group_id,
                    "summarizer": self._mode,
                }
                out.append(replace(bc, seq_num=seq, metadata=meta))
                seq += 1
            # Emit one coarse row per group
            joined = "\n\n".join(c.original_content for c in group_chunks)
            summary = self._summarize(joined, dict(doc.metadata or {}))
            out.append(Chunk(
                doc_id=doc.id,
                seq_num=seq,
                original_content=joined,
                embedded_content=summary,
                metadata={
                    "granularity": "coarse",
                    "group_id": group_id,
                    "summarizer": self._mode,
                    "strategy": "hierarchical_summary",
                },
            ))
            seq += 1
        return out

    def _group(self, chunks: list[Chunk]) -> list[list[Chunk]]:
        g = self.cfg.grouping
        if isinstance(g, FixedNGrouping):
            n = g.n
            return [chunks[i:i + n] for i in range(0, len(chunks), n)]
        if isinstance(g, WordBudgetGrouping):
            groups: list[list[Chunk]] = []
            cur: list[Chunk] = []
            cur_words = 0
            for c in chunks:
                w = len(c.original_content.split())
                if cur and cur_words + w > g.max_words:
                    groups.append(cur)
                    cur = [c]
                    cur_words = w
                else:
                    cur.append(c)
                    cur_words += w
            if cur:
                groups.append(cur)
            return groups
        if isinstance(g, SectionAwareGrouping):
            # base must be hierarchy; hierarchy chunks carry metadata.heading.
            groups: list[list[Chunk]] = []
            cur_heading = object()
            cur: list[Chunk] = []
            for c in chunks:
                h = c.metadata.get("heading")
                if h != cur_heading and cur:
                    groups.append(cur)
                    cur = []
                cur_heading = h
                cur.append(c)
            if cur:
                groups.append(cur)
            return groups
        raise ValueError(f"unknown grouping: {type(g).__name__}")
```

- [ ] **Step 4: Wire + run + commit**

```bash
uv run pytest tests/chunkshop/test_chunker_hierarchical_summary.py -v
git commit -m "feat(chunkers): HierarchicalSummaryChunker emits fine + coarse rows linked by group_id"
```

## ⛔ DC-003: Hierarchical

Verify SC-003 + SC-004 all three grouping strategies.

## Task 7: skimr integration end-to-end

**Files:**
- Create: `python/tests/chunkshop/test_skimr_integration_e2e.py`

- [ ] **Step 1: Write test**

```python
"""E2E: SummaryEmbedChunker wrapping hierarchy + skimr.summarize callable.
Skips cleanly if skimr isn't installed or Postgres is unreachable.
"""
import os
import pytest
pytest.importorskip("skimr")
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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_skimr_e2e CASCADE")
        conn.commit()


def test_summary_embed_with_skimr(ensure_pg):
    cfg = CellConfig(
        cell_name="skimr_e2e",
        source={"type": "files", "glob": "docs/samples/*-*.md", "id_from": "stem"},
        chunker={
            "type": "summary_embed",
            "base": {"type": "hierarchy"},
            "summarizer": {
                "mode": "callable",
                "module": "chunkshop.summarizers.skimr",
                "function": "summarize",
                "kwargs": {"max_length": 300},
            },
        },
        embedder={"type": "fastembed", "model_name": "Xenova/bge-small-en-v1.5-int8",
                  "dim": 384, "threads": 2},
        target={
            "dsn_env": DSN_ENV,
            "schema": "chunkshop_skimr_e2e",
            "table": "summarized",
            "mode": "create_if_missing",
            "source_tag": "skimr_test",
            "hnsw": False,
        },
    )
    result = run_cell(cfg)
    assert result.error is None, result.error

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT original_content, embedded_content, metadata->>'summarizer' "
            "FROM chunkshop_skimr_e2e.summarized LIMIT 5"
        )
        rows = cur.fetchall()
        assert len(rows) >= 1
        for orig, embedded, summarizer_mode in rows:
            assert summarizer_mode == "callable"
            # Summary should be shorter than the original (extractive summary of 300-char budget)
            assert len(embedded) <= len(orig) + 20  # allow slack for joins
            assert embedded != orig or len(orig) < 200  # short chunks may summarize to themselves
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/chunkshop/test_skimr_integration_e2e.py -v
git commit -m "test(e2e): summary_embed + skimr.summarize end-to-end through sink"
```

## ⛔ DC-004: Skimr E2E

Verify SC-005 with real skimr. Watch for any module-level skimr imports in chunkshop core.

## Task 8: Sample configs

**Files:**
- Create: `docs/samples/sample-summary-embed.yaml`
- Create: `docs/samples/sample-hierarchical.yaml`

- [ ] **Step 1: Write configs**

`sample-summary-embed.yaml`:

```yaml
cell_name: samples_summary_embed
source:
  type: files
  glob: docs/samples/*-*.md
  id_from: stem
chunker:
  type: summary_embed
  base:
    type: hierarchy
  summarizer:
    mode: callable
    module: chunkshop.summarizers.skimr
    function: summarize
    kwargs:
      max_length: 300
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_samples
  table: summary_embed
  mode: create_if_missing
  source_tag: summary_demo
  hnsw: false
```

`sample-hierarchical.yaml`:

```yaml
cell_name: samples_hierarchical
source:
  type: files
  glob: docs/samples/*-*.md
  id_from: stem
chunker:
  type: hierarchical_summary
  base:
    type: hierarchy
  summarizer:
    mode: callable
    module: chunkshop.summarizers.sumy
    function: summarize
    kwargs:
      algorithm: lex_rank
      sentences_count: 2
  grouping:
    strategy: section_aware
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_samples
  table: hierarchical
  mode: create_if_missing
  source_tag: hierarchical_demo
  promote_metadata:
    - path: granularity
      type: text
    - path: group_id
      type: text
  hnsw: false
```

- [ ] **Step 2: Verify both parse + commit**

```bash
uv run python -c "
from chunkshop.config import load_config
for p in ['docs/samples/sample-summary-embed.yaml', 'docs/samples/sample-hierarchical.yaml']:
    cfg = load_config(p)
    print(f'OK {p}  chunker={cfg.chunker.type}')
"
git commit -m "docs(samples): sample-summary-embed + sample-hierarchical configs"
```

## Task 9: `docs/summaries.md` reference

**Files:**
- Create: `docs/summaries.md`

Cover:
- When to embed summaries vs raw (retrieval-quality tradeoffs).
- `summary_embed` mechanics (original_content vs embedded_content).
- `hierarchical_summary` mechanics (fine + coarse + group_id).
- All three summarizer modes (external/callable/passthrough) with YAML recipes.
- All three grouping strategies.
- Decision matrix: skimr (deterministic, sub-ms, zero-dep) vs sumy (pluggable algos, ms-scale, needs NLTK) vs skimr-neural (abstractive, neural, when available) vs external (upstream-computed) vs user-wired (LLM API).
- Retrieval-side considerations: how to query fine-only, coarse-only, or hybrid.
- Promoting `granularity` + `group_id` to columns for GIN-indexed filtering.

- [ ] **Step 1: Write**
- [ ] **Step 2: Commit**

```bash
git commit -m "docs(summaries): reference for summary_embed + hierarchical_summary"
```

## Task 10: `docs/tutorial-summaries.md` narrative walkthrough

**Files:**
- Create: `docs/tutorial-summaries.md`

Step-by-step:
1. Prereqs (chunkshop, skimr via `pip install chunkshop[skimr]` or sibling path dep, Postgres).
2. Copy `sample-summary-embed.yaml`; ingest `docs/samples/*-*.md` with skimr callable.
3. Inspect rows: confirm `original_content` ≠ `embedded_content`; show lengths.
4. Semantic query against summary-embedded chunks vs raw (comparison if possible).
5. Copy `sample-hierarchical.yaml`; ingest into same or adjacent table with `mode: append`.
6. Query coarse granularity only: `WHERE granularity = 'coarse' ORDER BY embedding <=> qvec`.
7. Query fine, then pull coarse for context: join on `group_id`.
8. Swap skimr for sumy: YAML diff is one line (`module: chunkshop.summarizers.sumy` + `kwargs: {algorithm: text_rank}`). Rerun.
9. Decision matrix recap: when skimr, when sumy, when external, when to wire a custom LLM callable.

- [ ] **Step 1: Write**
- [ ] **Step 2: Commit**

```bash
git commit -m "docs(tutorial): summary-embed + hierarchical walkthrough with skimr + sumy"
```

## ⛔ DC-FINAL

- [ ] Every SC-001…SC-010 + SC-005b evidenced.
- [ ] Full suite green with optional deps available (`uv sync --extra dev --extra extractors --extra sumy`).
- [ ] Both sample YAMLs run end-to-end against a live Postgres.
- [ ] Tutorial executes verbatim.

## Notes for the executing agent

- **Worktree:** `../chunkshop-summary-embed -b feat/summary-embed`.
- **Depends on:** schema-flexibility (merged).
- **Independent of:** semantic chunker, metadata extractors, DocFramer.
- **skimr path dependency:** the `[tool.uv.sources]` table needs the right path to the skimr repo. If the path is wrong in CI, skimr tests skip cleanly via `pytest.importorskip`.
- **Parallel pip extras:** if a user does `pip install chunkshop[summarize]` we could auto-pull both skimr and sumy. Current design leaves that to `[skimr]` + `[sumy]` + future `[summarize]` umbrella. Document.

## Follow-ups (NOT this plan)

- `skimr-neural` adapter shim once the sibling repo ships.
- LLM-callable template (`chunkshop.summarizers.llm_openai`, `chunkshop.summarizers.llm_anthropic`) that hits APIs — explicitly opt-in, requires env-var credentials, and goes into retrieval-quality benchmarks to justify the cost.
- Caching layer: summary-cache keyed on chunk hash so repeated ingest doesn't re-summarize.
- Retrieval-side helpers (query templates for fine-only, coarse-first-then-fine fusion).
