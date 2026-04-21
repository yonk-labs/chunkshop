# chunkshop Semantic Chunker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `semantic` chunker that splits documents at topic shifts, detected by embedding similarity drops between adjacent sentences. Uses a dedicated small boundary model by default (`all-MiniLM-L6-v2-int8`) for processing speed. Option to reuse the cell's main embedder for memory-constrained environments.

**Architecture:** New `SemanticChunker` in `python/src/chunkshop/chunkers/semantic.py`. Configurable boundary model, breakpoint percentile threshold, min/max chunk bounds. Register `sentence-transformers/all-MiniLM-L6-v2-int8` in `embedders/_registry.py` if fastembed doesn't ship it. New speed-gated benchmark test. Docs section in `chunkers.md` + standalone tutorial.

**Tech Stack:** Python 3.12, fastembed (already a dep), numpy, optional NLTK for better sentence splitting, pytest.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-semantic-chunker.md`. Implements SC-001…SC-009. Independent — can be shipped in parallel with any other brief.

---

## Prerequisites

- Schema-flexibility merged (provides the extractor contract; not strictly required for chunker work but it's on main now).
- `cd chunkshop/python && uv sync --extra dev --extra extractors` completed.
- Postgres optional — unit tests cover correctness; the tutorial integration test needs a reachable DB.

## File Structure

**New files:**
- `python/src/chunkshop/chunkers/semantic.py`
- `python/tests/chunkshop/test_chunker_semantic.py`
- `python/tests/chunkshop/test_chunker_semantic_benchmark.py`
- `docs/tutorial-semantic.md`

**Modified files:**
- `python/src/chunkshop/config.py` — new `SemanticChunker` pydantic model in `ChunkerConfig` union.
- `python/src/chunkshop/chunkers/__init__.py` — `load_chunker` dispatch.
- `python/src/chunkshop/embedders/_registry.py` — register `all-MiniLM-L6-v2-int8` if missing.
- `docs/chunkers.md` — add semantic section + update decision tree.
- `docs/samples/` — optional: add `sample-semantic.yaml` for demo.

---

## Task 1: Context check + boundary model registration

**Files:**
- Modify: `python/src/chunkshop/embedders/_registry.py`
- Modify: `python/tests/chunkshop/test_int8_registry.py`

- [ ] **Step 1: Baseline**

`cd python && uv run pytest -q` — note the passing count. Must not regress.

- [ ] **Step 2: Check if fastembed ships `all-MiniLM-L6-v2` int8**

```bash
uv run python -c "
from fastembed import TextEmbedding
for m in TextEmbedding.list_supported_models():
    if 'minilm' in m['model'].lower():
        print(m['model'], m.get('dim'))
"
```

If you see `sentence-transformers/all-MiniLM-L6-v2-int8` or similar, skip Task 1 Step 3. If not, proceed.

- [ ] **Step 3: Register the int8 MiniLM variant**

In `embedders/_registry.py`, add an entry to `_INT8_VARIANTS`:

```python
{
    "model": "sentence-transformers/all-MiniLM-L6-v2-int8",
    "dim": 384,
    "pooling": PoolingType.MEAN,  # MiniLM uses mean pooling; verify on model card
    "normalization": True,
    "sources": ModelSource(hf="Xenova/all-MiniLM-L6-v2"),
    "model_file": "onnx/model_quantized.onnx",
    "description": "all-MiniLM-L6-v2 pre-quantized to int8 (Xenova upload)",
    "license": "apache-2.0",
    "size_in_gb": 0.022,
    "additional_files": [
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "config.json",
    ],
},
```

- [ ] **Step 4: Add registry test**

Append to `python/tests/chunkshop/test_int8_registry.py`:

```python
def test_minilm_int8_registered():
    from fastembed import TextEmbedding
    from chunkshop.embedders import register_int8_variants
    register_int8_variants()
    names = {m["model"] for m in TextEmbedding.list_supported_models()}
    assert "sentence-transformers/all-MiniLM-L6-v2-int8" in names
```

- [ ] **Step 5: Run — expect pass**

`uv run pytest tests/chunkshop/test_int8_registry.py -v`

- [ ] **Step 6: Commit**

```bash
git add python/src/chunkshop/embedders/_registry.py python/tests/chunkshop/test_int8_registry.py
git commit -m "feat(embedders): register all-MiniLM-L6-v2-int8 (boundary model for semantic chunker)"
```

## Task 2: Pydantic config for `SemanticChunker`

**Files:**
- Modify: `python/src/chunkshop/config.py`

- [ ] **Step 1: Write failing config test**

Create or append to `python/tests/chunkshop/test_config_semantic_chunker.py`:

```python
import pytest
from pydantic import ValidationError

from chunkshop.config import SemanticChunker


def test_semantic_chunker_defaults():
    c = SemanticChunker(type="semantic")
    assert c.boundary_model == "sentence-transformers/all-MiniLM-L6-v2-int8"
    assert c.breakpoint_percentile == 95
    assert c.min_sentences_per_chunk == 3
    assert c.max_chunk_chars == 3000
    assert c.sentence_splitter == "naive"


def test_semantic_chunker_same_boundary_model():
    c = SemanticChunker(type="semantic", boundary_model="same")
    assert c.boundary_model == "same"


def test_semantic_chunker_rejects_bad_percentile():
    with pytest.raises(ValidationError):
        SemanticChunker(type="semantic", breakpoint_percentile=150)
    with pytest.raises(ValidationError):
        SemanticChunker(type="semantic", breakpoint_percentile=0)
```

- [ ] **Step 2: Run — expect FAIL (ImportError)**

- [ ] **Step 3: Add `SemanticChunker` to `config.py`**

Near the other chunker models:

```python
class SemanticChunker(_Base):
    type: Literal["semantic"]
    boundary_model: str = "sentence-transformers/all-MiniLM-L6-v2-int8"
    breakpoint_percentile: int = Field(default=95, ge=1, le=99)
    min_sentences_per_chunk: int = Field(default=3, ge=1)
    max_chunk_chars: int = Field(default=3000, ge=100)
    sentence_splitter: Literal["naive", "nltk"] = "naive"
```

Add `SemanticChunker` to the `ChunkerConfig` union.

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_config_semantic_chunker.py
git commit -m "feat(config): SemanticChunker model with boundary_model + percentile knobs"
```

## Task 3: Sentence splitting helpers

**Files:**
- Create: `python/src/chunkshop/chunkers/_sentence_split.py`
- Create: `python/tests/chunkshop/test_sentence_split.py`

The semantic chunker needs a sentence splitter. Keep it simple: naive splits on `.`, `?`, `!` + whitespace. Optional NLTK splitter for users who install the `[extractors]` extra.

- [ ] **Step 1: Write tests**

```python
from chunkshop.chunkers._sentence_split import naive_sentences, load_sentence_splitter


def test_naive_splits_on_terminators():
    text = "First sentence. Second sentence! Third? Fourth sentence."
    sents = naive_sentences(text)
    assert len(sents) == 4
    assert sents[0].startswith("First")
    assert sents[1].startswith("Second")


def test_naive_handles_no_terminators():
    sents = naive_sentences("just some words with no terminator")
    assert sents == ["just some words with no terminator"]


def test_naive_strips_empty():
    sents = naive_sentences("One. . Two.")
    assert len(sents) == 2


def test_load_sentence_splitter_naive():
    fn = load_sentence_splitter("naive")
    assert fn("A. B. C.") == ["A.", "B.", "C."]
```

- [ ] **Step 2: Implement**

```python
from __future__ import annotations
import re


_TERMINATOR = re.compile(r"(?<=[.!?])\s+")


def naive_sentences(text: str) -> list[str]:
    """Split on sentence terminators (., !, ?) followed by whitespace.

    Not ML-aware — won't handle "Dr." or "e.g." correctly. Good enough for the
    boundary-detection use case where exact sentence identity doesn't matter.
    """
    parts = _TERMINATOR.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _nltk_sentences(text: str) -> list[str]:
    import nltk
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        nltk.download("punkt_tab", quiet=True)
    from nltk.tokenize import sent_tokenize
    return [s.strip() for s in sent_tokenize(text) if s.strip()]


def load_sentence_splitter(kind: str):
    if kind == "naive":
        return naive_sentences
    if kind == "nltk":
        return _nltk_sentences
    raise ValueError(f"unknown sentence_splitter: {kind!r}")
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/chunkshop/test_sentence_split.py -v
git commit -m "feat(chunkers): sentence splitting helpers (naive + nltk)"
```

## Task 4: `SemanticChunker` core boundary detection

**Files:**
- Create: `python/src/chunkshop/chunkers/semantic.py`
- Create: `python/tests/chunkshop/test_chunker_semantic.py`

- [ ] **Step 1: Write correctness tests**

Create `test_chunker_semantic.py`:

```python
import pytest
from chunkshop.chunkers.semantic import SemanticChunker
from chunkshop.config import SemanticChunker as Cfg
from chunkshop.sources.base import Document


# Fixture: three distinct topics with clear semantic shifts.
THREE_TOPIC_DOC = (
    "Neural networks are trained via backpropagation. Weights update based on loss gradients. "
    "Optimizers like Adam adapt learning rates per parameter. "
    "The golden retriever fetches the ball and wags its tail. Dogs are social animals that bond with humans. "
    "Border collies are particularly intelligent and trainable. "
    "Bread dough needs gluten development for structure. Knead the dough until it passes the windowpane test. "
    "Let it rise until doubled, then shape and proof before baking."
)


def test_semantic_finds_three_topic_boundaries():
    chunker = SemanticChunker(Cfg(
        type="semantic",
        breakpoint_percentile=66,   # looser for this short test
        min_sentences_per_chunk=2,
    ))
    doc = Document(id="d1", content=THREE_TOPIC_DOC, title="mixed", metadata={})
    chunks = chunker.chunk(doc)
    # Should produce ~3 chunks (one per topic). Allow 2-4 given percentile sensitivity.
    assert 2 <= len(chunks) <= 4
    for c in chunks:
        assert c.metadata.get("strategy") == "semantic"
        assert len(c.original_content) <= 3000  # max_chunk_chars


def test_semantic_single_topic_short_doc_returns_one_chunk():
    chunker = SemanticChunker(Cfg(type="semantic", min_sentences_per_chunk=1))
    doc = Document(id="d1", content="Just one sentence here. And another on the same topic.",
                   title="t", metadata={})
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1


def test_semantic_max_chars_clipping():
    """Oversized semantic segment hard-splits on sentence boundary."""
    long_text = " ".join(["The quick brown fox jumps."] * 300)  # ~7500 chars, one topic
    chunker = SemanticChunker(Cfg(type="semantic", max_chunk_chars=2000, min_sentences_per_chunk=1))
    doc = Document(id="d1", content=long_text, title="t", metadata={})
    chunks = chunker.chunk(doc)
    assert all(len(c.original_content) <= 2000 for c in chunks)


def test_semantic_empty_content_returns_no_chunks():
    chunker = SemanticChunker(Cfg(type="semantic"))
    doc = Document(id="d1", content="", title="t", metadata={})
    assert chunker.chunk(doc) == []
```

- [ ] **Step 2: Implement**

```python
from __future__ import annotations

import numpy as np

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._sentence_split import load_sentence_splitter
from chunkshop.config import SemanticChunker as Cfg
from chunkshop.sources.base import Document


class SemanticChunker:
    """Split a document at topic shifts detected by embedding similarity drops.

    Algorithm:
      1. Split content into sentences.
      2. Embed each sentence with a dedicated small boundary model (or main
         embedder when boundary_model='same').
      3. Compute cosine distance between adjacent sentence embeddings.
      4. Breakpoints = positions where distance >= percentile-threshold.
      5. Emit one chunk per span, enforcing min_sentences and max_chars.

    Deterministic given same input + same model.
    """

    def __init__(self, cfg: Cfg, main_embedder_model_name: str | None = None):
        self.cfg = cfg
        self._split = load_sentence_splitter(cfg.sentence_splitter)
        model_name = cfg.boundary_model
        if model_name == "same":
            if main_embedder_model_name is None:
                raise ValueError(
                    "SemanticChunker(boundary_model='same') requires main_embedder_model_name"
                )
            model_name = main_embedder_model_name
        self._model_name = model_name
        # Lazy model instantiation so unit tests can monkeypatch
        self._model = None

    def _get_model(self):
        if self._model is None:
            from fastembed import TextEmbedding
            # Register chunkshop's int8 variants idempotently
            from chunkshop.embedders import register_int8_variants
            register_int8_variants()
            self._model = TextEmbedding(model_name=self._model_name, threads=2)
        return self._model

    def chunk(self, doc: Document) -> list[Chunk]:
        if not doc.content or not doc.content.strip():
            return []
        sentences = self._split(doc.content)
        if not sentences:
            return []
        if len(sentences) == 1:
            text = sentences[0]
            return [self._mk_chunk(doc.id, 0, text)]

        model = self._get_model()
        embeddings = np.asarray(list(model.embed(sentences)), dtype=np.float32)
        # Normalize (bge/minilm already normalize; safe idempotent op)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embeddings = embeddings / norms

        # Cosine distance between adjacent sentences
        cos_sim = (embeddings[:-1] * embeddings[1:]).sum(axis=1)
        distances = 1.0 - cos_sim

        threshold = float(np.percentile(distances, self.cfg.breakpoint_percentile))
        # Breakpoints = indices i where distances[i] >= threshold (break AFTER sentence i)
        breakpoints = [i for i, d in enumerate(distances) if d >= threshold]
        # Build initial spans
        starts = [0]
        for bp in breakpoints:
            starts.append(bp + 1)
        spans = []
        for i, s in enumerate(starts):
            e = starts[i + 1] if i + 1 < len(starts) else len(sentences)
            spans.append((s, e))

        # Enforce min_sentences_per_chunk by merging small spans forward (or backward for last)
        spans = self._merge_small(spans, self.cfg.min_sentences_per_chunk)

        # Build chunks with max_chars clipping
        chunks: list[Chunk] = []
        for (s, e) in spans:
            body = " ".join(sentences[s:e]).strip()
            if not body:
                continue
            for sub in self._split_if_too_large(body, self.cfg.max_chunk_chars):
                chunks.append(self._mk_chunk(doc.id, len(chunks), sub))
        return chunks

    def _merge_small(self, spans, min_sents):
        if not spans:
            return spans
        merged = []
        for (s, e) in spans:
            if merged and (e - s) < min_sents:
                ps, pe = merged[-1]
                merged[-1] = (ps, e)
            else:
                merged.append((s, e))
        # Edge case: if the first span is below threshold and we never merged into it
        if len(merged) > 1 and (merged[0][1] - merged[0][0]) < min_sents:
            merged[0] = (merged[0][0], merged[1][1])
            merged.pop(1)
        return merged

    def _split_if_too_large(self, body: str, max_chars: int) -> list[str]:
        if len(body) <= max_chars:
            return [body]
        sents = self._split(body)
        out, cur = [], ""
        for s in sents:
            if len(cur) + len(s) + 1 > max_chars and cur:
                out.append(cur.strip())
                cur = s
            else:
                cur = (cur + " " + s).strip() if cur else s
        if cur:
            out.append(cur.strip())
        return out

    def _mk_chunk(self, doc_id: str, seq: int, text: str) -> Chunk:
        return Chunk(
            doc_id=doc_id,
            seq_num=seq,
            original_content=text,
            embedded_content=text,
            metadata={"strategy": "semantic"},
        )
```

- [ ] **Step 3: Wire into `load_chunker`**

In `chunkers/__init__.py`:

```python
from chunkshop.chunkers.semantic import SemanticChunker
from chunkshop.config import SemanticChunker as SemanticCfg

# in load_chunker:
    if isinstance(cfg, SemanticCfg):
        return SemanticChunker(cfg, main_embedder_model_name=...)  # See below
```

**Tricky bit:** when `boundary_model="same"`, the chunker needs to know the main embedder's model name. The `load_chunker` signature doesn't have that today. Two options:

a. Extend `load_chunker(cfg, *, main_embedder: EmbedderConfig = None)` and pass from `runner.run_cell`.
b. Defer the "same" resolution to the runner — the runner instantiates the chunker with an extra arg.

Go with (a). Cleaner. `runner.run_cell` becomes:

```python
chunker = load_chunker(cfg.chunker, main_embedder=cfg.embedder)
```

Update `load_chunker` signature in `chunkers/__init__.py`:

```python
def load_chunker(cfg: ChunkerConfig, *, main_embedder=None) -> Chunker:
    ...
    if isinstance(cfg, SemanticCfg):
        main_model = getattr(main_embedder, "model_name", None) if main_embedder else None
        return SemanticChunker(cfg, main_embedder_model_name=main_model)
    ...
```

Update `runner.run_cell` to pass `main_embedder=cfg.embedder`.

For `NeighborExpandChunker` which recursively calls `load_chunker(cfg.base)`: propagate `main_embedder` too.

- [ ] **Step 4: Run — expect PASS (will download MiniLM model on first run — ~20 MB)**

```bash
uv run pytest tests/chunkshop/test_chunker_semantic.py -v
```

- [ ] **Step 5: Full-suite regression**

`uv run pytest -q` — no existing tests should break.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(chunkers): SemanticChunker via sentence-embedding boundary detection"
```

## ⛔ DC-001: Semantic correctness

Re-read brief. Verify SC-001, SC-004, SC-005.

## Task 5: Speed benchmark (SC-003)

**Files:**
- Create: `python/tests/chunkshop/test_chunker_semantic_benchmark.py`

The brief's SC-003 gate: semantic chunking on a 5000-word doc completes in ≤ 2× the time of fastembed embedding the same content. Measured in CI.

- [ ] **Step 1: Write benchmark test**

```python
"""Speed gate (SC-003): semantic chunking wall time <= 2 * main embed wall time.

Uses the shipped docs/samples/*-*.md concatenated to ~5000 words. Runs the
chunker with its default dedicated boundary model (MiniLM int8). Measures
the main-cell embed time by running the same content through the cell's
primary embedder (bge-small int8).
"""
import os
import glob
import time
import pytest
import numpy as np

from chunkshop.chunkers.semantic import SemanticChunker
from chunkshop.config import SemanticChunker as Cfg
from chunkshop.sources.base import Document


def _load_5k_word_text():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    files = sorted(glob.glob(os.path.join(repo_root, "docs", "samples", "*-*.md")))
    combined = "\n\n".join(open(f).read() for f in files)
    words = combined.split()
    # Replicate to ~5000 words if shorter
    while len(words) < 5000:
        words += combined.split()
    return " ".join(words[:5000])


@pytest.mark.slow
def test_semantic_chunking_speed_gate():
    text = _load_5k_word_text()
    doc = Document(id="bench", content=text, title="bench", metadata={})

    # Main-cell embed time: embed the whole text as one batch with bge-small int8
    from fastembed import TextEmbedding
    import chunkshop.embedders  # register int8

    main_model = TextEmbedding(model_name="Xenova/bge-small-en-v1.5-int8", threads=2)
    t0 = time.perf_counter()
    _ = list(main_model.embed([text]))
    main_embed_time = time.perf_counter() - t0

    # Warm the MiniLM model (first load downloads)
    boundary = TextEmbedding(
        model_name="sentence-transformers/all-MiniLM-L6-v2-int8", threads=2
    )
    _ = list(boundary.embed(["warmup"]))

    # Semantic chunking time
    chunker = SemanticChunker(Cfg(type="semantic"))
    t0 = time.perf_counter()
    chunks = chunker.chunk(doc)
    semantic_time = time.perf_counter() - t0

    print(f"main embed: {main_embed_time:.2f}s, semantic chunk: {semantic_time:.2f}s, "
          f"ratio: {semantic_time / main_embed_time:.2f}x")
    assert semantic_time <= 2 * main_embed_time, (
        f"semantic chunking too slow: {semantic_time:.2f}s vs {2 * main_embed_time:.2f}s ceiling"
    )
    assert len(chunks) > 1
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/chunkshop/test_chunker_semantic_benchmark.py -v -s
```

Read the printed ratio. If it fails, instrument: sentence splitting is usually <1% of the time; the bottleneck is almost always the sentence-level embed batch. MiniLM-int8 should be ~3x faster than bge-small-int8 per token, so the ratio should land ~1.0-1.5x on typical CPU hardware.

If benchmark fails on a specific machine, don't silently loosen the gate — first check whether the machine has unusually slow CPU or the model isn't quantized correctly.

- [ ] **Step 3: Commit**

```bash
git commit -m "test(semantic): speed gate — chunking <= 2x main-embed wall time (SC-003)"
```

## ⛔ DC-002: Speed gate

Brief requires SC-003 empirically verified. Run the benchmark locally BEFORE writing the tutorial. If it fails, either:
- Tune defaults (reduce batch_size, optimize sentence splitting) and re-measure.
- Propose loosening to 3× and negotiate with the human.

## Task 6: Update `docs/chunkers.md`

**Files:**
- Modify: `docs/chunkers.md`

- [ ] **Step 1: Update decision tree mermaid**

Add a new branch for "unstructured prose / transcript / interview → `semantic`".

- [ ] **Step 2: Add `## 5. semantic — topic-shift boundary detection` section**

Cover: what it does, knobs (boundary_model, breakpoint_percentile, min/max), when to pick, when not to, sample output, `boundary_model: "same"` memory trick, tuning advice ("chunks too small → raise percentile; too large → lower").

- [ ] **Step 3: Commit**

```bash
git commit -m "docs(chunkers): add semantic chunker section + decision tree update"
```

## Task 7: Tutorial — transcript chunking demo

**Files:**
- Create: `docs/samples/transcript.md`
- Create: `docs/tutorial-semantic.md`
- Optional: `docs/samples/sample-semantic.yaml`

- [ ] **Step 1: Create a transcript fixture**

`docs/samples/transcript.md` — ~500 words of fictitious meeting transcript with 3-4 real topic shifts (planning, blocker discussion, decision, next steps). No markdown headings. This gives `semantic` something meaningful to split.

Example:

```markdown
So I wanted to walk through the Q3 roadmap. We're still planning to ship the ingest
rewrite by mid-September. The embedding pipeline work is mostly done — Priya wrapped
up the int8 migration last week.

About the embedding work — we ran into one issue. The ONNX export for the newer model
had a dynamic axis that broke quantization. Priya's got a workaround using a fixed
sequence length, but it costs us about 5% throughput. Worth flagging for the eval.

Okay, let's pivot to the scheduling problem. We still owe Sales the beta invite list
by Friday. Can someone own that? ... Ravi, you good to pull it together? Great.
Deadline is EOW.

Last thing — the hiring rubric. We agreed last week to push the system design section
to the second round. HR needs the updated doc by Monday so we can send it to the next
batch of candidates. I'll ping you, Liam.
```

Keep it on one "doc" but with 4 internal topic shifts.

- [ ] **Step 2: Write `docs/tutorial-semantic.md`**

Walkthrough:
1. Prereqs (chunkshop installed, Postgres).
2. Ingest `docs/samples/transcript.md` three ways in parallel via `chunkshop orchestrate`:
   - semantic (new)
   - sentence_aware (baseline)
   - hierarchy (which emits 1 chunk since no headings)
3. Show chunk counts + measured ingest times.
4. Run a semantic query ("what's the Friday deadline about?") against each table — show top-1 results.
5. Show how to tune: raise/lower `breakpoint_percentile`, swap `boundary_model: "same"` to save RAM.

- [ ] **Step 3: Add `sample-semantic.yaml`**

```yaml
# docs/samples/sample-semantic.yaml
cell_name: samples_semantic
source:
  type: files
  glob: docs/samples/transcript.md
  id_from: stem
chunker:
  type: semantic
  breakpoint_percentile: 75    # looser for a short transcript
  min_sentences_per_chunk: 2
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 2
target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_samples
  table: transcript_semantic
  mode: create_if_missing
  source_tag: transcript_demo
  hnsw: false
```

- [ ] **Step 4: Commit**

```bash
git commit -m "docs(tutorial): semantic chunker walkthrough with transcript fixture"
```

## ⛔ DC-FINAL

- [ ] Every SC-001…SC-009 has evidence (test or doc).
- [ ] `uv run pytest -q` — all green.
- [ ] Benchmark runs under the 2× gate locally.
- [ ] Tutorial sample runs end-to-end.

## Notes for the executing agent

- **Worktree:** `../chunkshop-semantic -b feat/semantic-chunker`.
- **Model downloads:** MiniLM int8 (~22 MB) + bge-small int8 (already cached usually). ~50 MB total.
- **Runner change:** `load_chunker(cfg.chunker, main_embedder=cfg.embedder)` is a small API tweak. Update all callsites.
- **NeighborExpandChunker propagation:** if a semantic chunker is nested under neighbor_expand, the outer dispatch must forward `main_embedder`. Don't forget.

## Follow-ups (NOT this plan)

- Reuse boundary-detection embeddings as final chunk embeddings when `boundary_model == main_embedder.model_name` — avoids double-embedding. Nontrivial refactor.
- Add `--preview` CLI flag that shows where `semantic` would split without writing to DB.
- Explore sentence-window embeddings (pool across a 3-sentence window) for more stable boundary signals on noisy text.
- Cross-document boundary detection for podcasts / multi-speaker corpora.
