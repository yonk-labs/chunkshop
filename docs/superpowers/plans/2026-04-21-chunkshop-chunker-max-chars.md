# chunkshop Chunker max_chars Hotfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the default embedder's 512-token limit inside `HierarchyChunker` and `SentenceAwareChunker`. Today hierarchy emits whatever lives between headings (surfaced in the wild at 134 KB per section, silently truncated by bge-small to its first ~2 KB). sentence_aware has a hardcoded 3000-char cap, still over 512 tokens. Add a `max_chars: int = 2000` config field to both, hard-split oversized bodies via a shared paragraph→sentence→char helper, preserve heading metadata across split children, and document tuning per embedder.

**Architecture:** New shared splitter at `chunkshop/chunkers/_splitting.py`. `HierarchyChunker` calls the helper whenever a section body exceeds `max_chars`. `SentenceAwareChunker` drops module-level `_MAX_CHARS`/`_MIN_CHARS` in favor of `self.cfg.max_chars`/`self.cfg.min_chars`. No changes to `fixed_overlap`, `neighbor_expand`, embedders, or runner. `CellConfig` schema gains two optional fields (additive only; existing YAML continues to validate).

**Tech Stack:** Python 3.12, pydantic v2, re, pytest.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-chunker-max-chars.md`. This plan implements all 9 Success Criteria. Drift Checkpoints (DC-001…DC-FINAL) injected as ⛔ hard gates.

---

## Prerequisites

- chunkshop checked out on `main`. This plan is independent of DocFramer (merged), metadata-extractors, semantic-chunker, and summary-embed.
- `cd python && uv sync --extra dev` completed.
- Test baseline: 61 passing (post-DocFramer merge).

## File Structure

**New files:**
- `python/src/chunkshop/chunkers/_splitting.py` — shared paragraph→sentence→char splitter.
- `python/tests/chunkshop/test_splitting.py` — unit tests for the helper (SC-003).

**Modified files:**
- `python/src/chunkshop/config.py` — add `max_chars` + `min_chars` to `SentenceAwareChunker` and `max_chars` to `HierarchyChunker`.
- `python/src/chunkshop/chunkers/sentence_aware.py` — replace module constants with `self.cfg` reads; use shared splitter.
- `python/src/chunkshop/chunkers/hierarchy.py` — hard-split oversized section bodies; add `section_part` metadata on split children.
- `python/tests/chunkshop/test_chunkers.py` — add hierarchy-splits-oversized-sections test + sentence-aware-respects-max test.
- `docs/chunkers.md` — add `max_chars` rows to both knob tables; add "Tuning for your embedder" subsection; update the line-215 warning.
- `docs/superpowers/plans/2026-04-20-chunkshop-semantic-chunker.md` — one-line annotation noting the Brief-3 `max_chunk_chars` default should drop to 2000 (SC-008, annotation only).
- `README.md` or equivalent release-notes location — behavior-change line (SC-009).

---

## Task 1: Context check + baseline

**Files:** (read-only)

- [ ] **Step 1: Verify baseline tests pass**

```bash
cd python && uv run pytest -q
```

Expected: `61 passed` (post-DocFramer merge count). Note exact count — this plan must preserve or grow it.

- [ ] **Step 2: Skim current chunker code**

Read: `src/chunkshop/chunkers/hierarchy.py`, `src/chunkshop/chunkers/sentence_aware.py`, `src/chunkshop/config.py` (lines 61–88, chunker configs). Confirm:
- `hierarchy.py` has no size cap on section bodies.
- `sentence_aware.py` has module constants `_MAX_CHARS = 3000` and `_MIN_CHARS = 200`.
- `config.py` defines both as pydantic models with `_Base` parent.

- [ ] **Step 3: Skim current chunker docs**

Read: `docs/chunkers.md` — note table format for knobs, existing warning at line 215 about 2000-char base chunkers blowing past token limits.

## Task 2: Shared paragraph→sentence→char splitter

**Files:**
- Create: `python/src/chunkshop/chunkers/_splitting.py`
- Create: `python/tests/chunkshop/test_splitting.py`

- [ ] **Step 1: Write failing tests first (SC-003)**

Create `python/tests/chunkshop/test_splitting.py`:

```python
from chunkshop.chunkers._splitting import split_to_max_chars


def test_returns_single_chunk_when_under_max():
    text = "short text"
    out = split_to_max_chars(text, max_chars=100)
    assert out == ["short text"]


def test_splits_on_paragraph_boundaries_when_possible():
    p1 = "First paragraph about alpha." + " alpha" * 50
    p2 = "Second paragraph about beta." + " beta" * 50
    p3 = "Third paragraph about gamma." + " gamma" * 50
    text = f"{p1}\n\n{p2}\n\n{p3}"
    out = split_to_max_chars(text, max_chars=len(p1) + 50)
    assert len(out) >= 2
    # No chunk should straddle a paragraph break
    for chunk in out:
        assert not (p1.strip() in chunk and p3.strip() in chunk)


def test_splits_on_sentence_boundaries_when_paragraph_too_big():
    # One paragraph, multiple sentences, whole paragraph exceeds max
    sentences = [f"This is sentence number {i}." for i in range(20)]
    text = " ".join(sentences)
    out = split_to_max_chars(text, max_chars=150)
    assert len(out) >= 2
    for chunk in out:
        assert len(chunk) <= 150
    # Each chunk should end with sentence punctuation (., !, ?) or be last
    for chunk in out[:-1]:
        assert chunk.rstrip().endswith((".", "!", "?"))


def test_falls_back_to_char_slice_when_no_punctuation():
    text = "a" * 500
    out = split_to_max_chars(text, max_chars=100)
    assert len(out) == 5
    for chunk in out:
        assert len(chunk) <= 100
    assert "".join(out) == text


def test_preserves_content_fully():
    text = "Paragraph one.\n\nParagraph two has more text here. Sentence two. Sentence three."
    out = split_to_max_chars(text, max_chars=40)
    # Whitespace-normalized concatenation must match whitespace-normalized input
    import re
    assert re.sub(r"\s+", " ", "".join(out)).strip() == re.sub(r"\s+", " ", text).strip()


def test_respects_max_strictly():
    # Even with pathological single-word text, no output chunk exceeds max
    text = "word " * 1000
    out = split_to_max_chars(text, max_chars=50)
    for chunk in out:
        assert len(chunk) <= 50
```

Run: `cd python && uv run pytest tests/chunkshop/test_splitting.py -q` — expect failure (module doesn't exist yet).

- [ ] **Step 2: Implement the splitter**

Create `python/src/chunkshop/chunkers/_splitting.py`:

```python
"""Shared paragraph -> sentence -> char splitter.

Used by HierarchyChunker and SentenceAwareChunker to hard-cap chunk size at
the embedder's token budget. Prefers semantic boundaries (blank-line paragraph
breaks, sentence-ending punctuation) before falling back to character slicing.
"""
from __future__ import annotations
import re

_PARA_BREAK = re.compile(r"\n\s*\n")
_SENT_BREAK = re.compile(r"(?<=[.!?])\s+")


def _char_slice(text: str, max_chars: int) -> list[str]:
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _split_sentences(text: str, max_chars: int) -> list[str]:
    sentences = _SENT_BREAK.split(text)
    out: list[str] = []
    buf = ""
    for s in sentences:
        if not s:
            continue
        # Sentence itself too big -> hard char-slice it
        if len(s) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_char_slice(s, max_chars))
            continue
        candidate = f"{buf} {s}".strip() if buf else s
        if len(candidate) > max_chars:
            if buf:
                out.append(buf)
            buf = s
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out


def split_to_max_chars(text: str, max_chars: int) -> list[str]:
    """Split text into chunks no larger than max_chars.

    Order: paragraph boundaries -> sentence boundaries -> character slice.
    Guarantees: every char of input appears in some output chunk (whitespace
    between paragraphs/sentences may be normalized).
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in _PARA_BREAK.split(text) if p.strip()]
    out: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > max_chars:
            # Paragraph itself too big -> descend to sentence splitting
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_split_sentences(para, max_chars))
            continue
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > max_chars:
            if buf:
                out.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out
```

- [ ] **Step 3: Run tests**

```bash
cd python && uv run pytest tests/chunkshop/test_splitting.py -q
```

Expected: all 6 tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/chunkshop/chunkers/_splitting.py tests/chunkshop/test_splitting.py
git commit -m "feat(chunkers): shared paragraph->sentence->char splitter (SC-003)"
```

## ⛔ DC-001 Drift Check

- Splitter tests pass in isolation.
- Helper is self-contained (no imports from `chunkshop.chunkers.*` classes).
- Full suite still green: `cd python && uv run pytest -q` shows 67+ passing (61 + 6 new).

## Task 3: Add max_chars to config models

**Files:**
- Modify: `python/src/chunkshop/config.py`

- [ ] **Step 1: Add fields to SentenceAwareChunker**

In `config.py` around line 61:

```python
class SentenceAwareChunker(_Base):
    type: Literal["sentence_aware"] = "sentence_aware"
    doc_type: Literal["prose", "code"] = "prose"
    max_chars: int = 2000
    min_chars: int = 200
```

- [ ] **Step 2: Add field to HierarchyChunker**

In `config.py` around line 72:

```python
class HierarchyChunker(_Base):
    type: Literal["hierarchy"]
    prefix_heading: bool = True
    min_section_chars: int = 100
    max_chars: int = 2000
```

- [ ] **Step 3: Sanity-check pydantic**

```bash
cd python && uv run python -c "
from chunkshop.config import SentenceAwareChunker, HierarchyChunker
print(SentenceAwareChunker().model_dump())
print(HierarchyChunker(type='hierarchy').model_dump())
"
```

Expected: both dumps include `max_chars: 2000` (and `min_chars: 200` for sentence_aware). Existing YAML that doesn't specify the field defaults cleanly.

- [ ] **Step 4: Full test suite still passes**

```bash
cd python && uv run pytest -q
```

Expected: 67+ passing, no regressions (config additions are pure additive defaults).

- [ ] **Step 5: Commit**

```bash
git add src/chunkshop/config.py
git commit -m "feat(config): max_chars/min_chars fields on sentence_aware and hierarchy chunker configs (SC-001, SC-002 config)"
```

## Task 4: Wire max_chars into SentenceAwareChunker

**Files:**
- Modify: `python/src/chunkshop/chunkers/sentence_aware.py`
- Modify: `python/tests/chunkshop/test_chunkers.py`

- [ ] **Step 1: Write failing test (SC-005)**

Append to `test_chunkers.py`:

```python
def test_sentence_aware_respects_configured_max_chars():
    # 10 KB doc with many sentences
    sentences = [f"Sentence number {i} with some filler words here." for i in range(400)]
    doc_text = " ".join(sentences)
    assert len(doc_text) > 10_000
    chunker = load_chunker(SentenceAwareChunker(max_chars=500, min_chars=50))
    chunks = chunker.chunk(_doc(doc_text))
    assert len(chunks) >= 10
    for c in chunks:
        assert len(c.embedded_content) <= 500
        assert len(c.original_content) <= 500
```

Run: `cd python && uv run pytest tests/chunkshop/test_chunkers.py::test_sentence_aware_respects_configured_max_chars -q` — expect failure (module constants still in use).

- [ ] **Step 2: Refactor sentence_aware.py**

Replace module constants with config reads. Rewrite `sentence_aware.py`:

```python
from __future__ import annotations
import re

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._splitting import split_to_max_chars
from chunkshop.config import SentenceAwareChunker as Cfg
from chunkshop.sources.base import Document


_MD_HEADING = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


def _split_plain(text: str, max_chars: int, min_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    result: list[str] = []
    buffer = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if buffer:
                result.append(buffer.strip())
                buffer = ""
            result.extend(split_to_max_chars(para, max_chars))
        elif len(buffer) + len(para) + 2 > max_chars and buffer:
            result.append(buffer.strip())
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer:
        result.append(buffer.strip())
    return result


def _split_prose(text: str, max_chars: int, min_chars: int) -> list[str]:
    headings = list(_MD_HEADING.finditer(text))
    if not headings:
        return _split_plain(text, max_chars, min_chars)
    result: list[str] = []
    for i, match in enumerate(headings):
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section = text[start:end].strip()
        if section:
            result.extend(split_to_max_chars(section, max_chars))
    if headings[0].start() > 0:
        prefix = text[: headings[0].start()].strip()
        if prefix:
            result = split_to_max_chars(prefix, max_chars) + result
    if len(text) <= max_chars:
        return [s for s in result if s]
    return [s for s in result if len(s) >= min_chars]


class SentenceAwareChunker:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def chunk(self, doc: Document) -> list[Chunk]:
        if self.cfg.doc_type == "code":
            splits = _split_plain(doc.content, self.cfg.max_chars, self.cfg.min_chars)
        else:
            splits = _split_prose(doc.content, self.cfg.max_chars, self.cfg.min_chars)
        return [
            Chunk(
                doc_id=doc.id,
                seq_num=i,
                original_content=text,
                embedded_content=text,
                metadata={"strategy": "sentence_aware"},
            )
            for i, text in enumerate(splits)
        ]
```

- [ ] **Step 3: Run tests**

```bash
cd python && uv run pytest tests/chunkshop/test_chunkers.py -q
```

Expected: all sentence_aware tests pass, including the new max_chars test.

- [ ] **Step 4: Full suite**

```bash
cd python && uv run pytest -q
```

Expected: 68+ passing (61 baseline + 6 splitter + 1 new sentence_aware = 68). No regressions.

- [ ] **Step 5: Commit**

```bash
git add src/chunkshop/chunkers/sentence_aware.py tests/chunkshop/test_chunkers.py
git commit -m "feat(chunkers): sentence_aware uses cfg.max_chars instead of module constant (SC-001, SC-005)"
```

## ⛔ DC-002 Drift Check

- Module-level `_MAX_CHARS` and `_MIN_CHARS` are gone from `sentence_aware.py`.
- Default `max_chars = 2000` does NOT break any existing fixture in the test suite.
- If DC-002 fails (e.g., an existing test relied on the 3000 default), STOP and diagnose. Do NOT revert the default — the whole point is the lower cap. Fix the fixture instead.

## Task 5: Wire max_chars into HierarchyChunker with section_part metadata

**Files:**
- Modify: `python/src/chunkshop/chunkers/hierarchy.py`
- Modify: `python/tests/chunkshop/test_chunkers.py`

- [ ] **Step 1: Write failing test (SC-004)**

Append to `test_chunkers.py`:

```python
def test_hierarchy_splits_oversized_sections():
    # One H1 with a 10 KB body
    body = ("This is a paragraph that talks about medical conditions. " * 30).strip()
    paragraphs = [body] * 10
    big_section = "\n\n".join(paragraphs)
    assert len(big_section) > 10_000
    md = f"# About Bladder Cancer\n\n{big_section}"
    chunker = load_chunker(HierarchyChunker(type="hierarchy", max_chars=2000))
    chunks = chunker.chunk(_doc(md))
    assert len(chunks) >= 5
    for c in chunks:
        # embedded_content includes the heading prefix (prefix_heading default True)
        # so the effective content limit is max_chars + heading overhead; check original
        assert len(c.original_content) <= 2000
        assert c.metadata["heading"] == "About Bladder Cancer"
    # section_part should be monotonically increasing from 0
    parts = [c.metadata.get("section_part") for c in chunks]
    assert parts == list(range(len(chunks)))


def test_hierarchy_non_oversized_sections_still_work():
    # Same body layout as the classic heading test — none oversized
    body_a = "alpha body text that is unambiguously longer than one hundred characters so the min_section_chars filter leaves it intact."
    body_b = "beta body text that is unambiguously longer than one hundred characters so the min_section_chars filter leaves it intact."
    md = f"# Section One\n\n{body_a}\n\n# Section Two\n\n{body_b}"
    chunker = load_chunker(HierarchyChunker(type="hierarchy"))
    chunks = chunker.chunk(_doc(md))
    assert len(chunks) == 2
    # Sections not split -> section_part is 0 on each
    for c in chunks:
        assert c.metadata.get("section_part") == 0
```

Run: `cd python && uv run pytest tests/chunkshop/test_chunkers.py::test_hierarchy_splits_oversized_sections -q` — expect failure.

- [ ] **Step 2: Refactor hierarchy.py**

Rewrite `hierarchy.py`:

```python
from __future__ import annotations
import re

from chunkshop.chunkers.base import Chunk
from chunkshop.chunkers._splitting import split_to_max_chars
from chunkshop.config import HierarchyChunker as Cfg
from chunkshop.sources.base import Document

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)$", re.MULTILINE)


def _emit_section_chunks(
    body: str,
    heading_text: str,
    doc_id: str,
    start_seq: int,
    prefix_heading: bool,
    max_chars: int,
) -> list[Chunk]:
    parts = split_to_max_chars(body, max_chars) if len(body) > max_chars else [body]
    chunks: list[Chunk] = []
    for i, part in enumerate(parts):
        embedded = f"{heading_text}\n\n{part}" if (heading_text and prefix_heading) else part
        chunks.append(Chunk(
            doc_id=doc_id,
            seq_num=start_seq + i,
            original_content=part,
            embedded_content=embedded,
            metadata={
                "strategy": "hierarchy",
                "heading": heading_text,
                "section_part": i,
            },
        ))
    return chunks


class HierarchyChunker:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def chunk(self, doc: Document) -> list[Chunk]:
        text = doc.content
        headings = list(_HEADING.finditer(text))
        if not headings:
            body = text.strip()
            if not body:
                return []
            return _emit_section_chunks(
                body=body,
                heading_text=doc.title or "",
                doc_id=doc.id,
                start_seq=0,
                prefix_heading=self.cfg.prefix_heading,
                max_chars=self.cfg.max_chars,
            )
        chunks: list[Chunk] = []
        if headings[0].start() > 0:
            body = text[: headings[0].start()].strip()
            if len(body) >= self.cfg.min_section_chars:
                chunks.extend(_emit_section_chunks(
                    body=body,
                    heading_text=doc.title or "",
                    doc_id=doc.id,
                    start_seq=len(chunks),
                    prefix_heading=self.cfg.prefix_heading,
                    max_chars=self.cfg.max_chars,
                ))
        for i, m in enumerate(headings):
            heading_text = m.group(2).strip()
            start = m.end()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[start:end].strip()
            if len(body) < self.cfg.min_section_chars:
                continue
            chunks.extend(_emit_section_chunks(
                body=body,
                heading_text=heading_text,
                doc_id=doc.id,
                start_seq=len(chunks),
                prefix_heading=self.cfg.prefix_heading,
                max_chars=self.cfg.max_chars,
            ))
        return chunks
```

- [ ] **Step 3: Run new hierarchy tests**

```bash
cd python && uv run pytest tests/chunkshop/test_chunkers.py -q
```

Expected: existing `test_hierarchy_prefixes_heading` still passes; new `test_hierarchy_splits_oversized_sections` and `test_hierarchy_non_oversized_sections_still_work` pass.

- [ ] **Step 4: Full suite**

```bash
cd python && uv run pytest -q
```

Expected: 70+ passing (61 + 6 + 1 + 2 = 70). No regressions.

- [ ] **Step 5: Commit**

```bash
git add src/chunkshop/chunkers/hierarchy.py tests/chunkshop/test_chunkers.py
git commit -m "feat(chunkers): hierarchy splits oversized sections, preserves heading + adds section_part (SC-002, SC-004)"
```

## ⛔ DC-003 Drift Check

- `test_hierarchy_prefixes_heading` (pre-existing) still passes — split children inherit heading correctly.
- `test_hierarchy_splits_oversized_sections` passes — 10 KB section → ≥5 chunks, each ≤2000 chars, all carry the heading, `section_part` monotonic.
- `test_end_to_end_samples_corpus.py` passes — the 4-doc samples corpus is short-prose, no sections exceed 2000 chars, so chunk counts should be unchanged from baseline.

## Task 6: Sample-corpus chunk-count regression gate

**Files:** (read-only verification)

- [ ] **Step 1: Run sample-corpus test and note chunk counts**

```bash
cd python && uv run pytest tests/chunkshop/test_end_to_end_samples_corpus.py -q -v
```

Expected: test passes. If the test reports chunk counts (or you can inspect the Postgres table after), confirm they haven't drifted unexpectedly.

- [ ] **Step 2: Manual chunk-count diff (optional, only if DC-004 feels risky)**

Run `chunkshop ingest` against `docs/samples/sample-multi-source.yaml` (requires Postgres). Query `SELECT source, COUNT(*) FROM chunks_samples GROUP BY source` and record counts.

Expected: counts match a baseline run with the old defaults (samples/ corpus is small enough that all sections fit under 2000 chars).

## ⛔ DC-004 Drift Check

- Sample corpus chunk counts have NOT drifted (unexpected drift = the 2000 default is too aggressive for clean prose, and we need to revisit).
- If counts drifted: STOP. Either the samples have a >2000-char section (expected behavior — doc the drift), OR the splitter is over-splitting short prose (bug — fix before docs).

## Task 7: Update docs/chunkers.md

**Files:**
- Modify: `docs/chunkers.md`

- [ ] **Step 1: Add `max_chars` to SentenceAwareChunker knob table**

Find the `sentence_aware` knobs table. Add a row:

```
| `max_chars`   | `2000` | Hard cap on chunk size. See "Tuning for your embedder" below. |
| `min_chars`   | `200`  | Chunks below this are dropped when splitting a headed doc.    |
| `doc_type`    | `prose` | `prose` or `code`.                                           |
```

- [ ] **Step 2: Add `max_chars` to HierarchyChunker knob table**

Find the `hierarchy` knobs table. Add a row:

```
| `max_chars`         | `2000` | Hard cap; sections above are split on paragraph→sentence→char. |
| `prefix_heading`    | `true` | Prepend the heading text to `embedded_content`.                |
| `min_section_chars` | `100`  | Skip sections with bodies shorter than this.                   |
```

Also: document that split children carry `metadata.heading` (same as parent) and `metadata.section_part` (0-indexed).

- [ ] **Step 3: Add "Tuning for your embedder" subsection**

Place near the top of the chunker reference (before or after the chunker list, author's choice — match existing structure):

```markdown
## Tuning `max_chars` for your embedder

`max_chars` on `sentence_aware` and `hierarchy` enforces an upper bound so
chunks never exceed the embedder's token limit. Defaults target
`bge-small-en-v1.5` (512 tokens). If you're using a larger-context embedder,
raise it to match.

| Embedder                      | Token limit | Recommended `max_chars` |
|-------------------------------|-------------|-------------------------|
| `bge-small-en-v1.5`           | 512         | `2000` (default)        |
| `bge-base-en-v1.5`            | 512         | `2000`                  |
| `text-embedding-3-small`      | 8192        | `6000`                  |
| `text-embedding-3-large`      | 8192        | `6000`                  |

Character-to-token ratio is corpus-dependent (~4 chars/token for English prose;
less for code, URLs, or non-Latin scripts). Defaults leave headroom. If you
see truncation warnings from the embedder, lower `max_chars`.
```

- [ ] **Step 4: Rewrite the neighbor_expand warning at line 215**

Old:
```
- Your base chunker already emits long chunks (≥ 2000 chars) — you'll blow past the
  embedder's token limit. `bge-small-en-v1.5` truncates at 512 tokens.
```

New:
```
- Your base chunker's `max_chars` plus `window` × neighbor size exceeds your
  embedder's token budget. With the default `max_chars: 2000` and `window: 1`,
  each neighbor-expanded embed concatenates up to ~6000 chars (~1500 tokens) —
  safe for `bge-small-en-v1.5` at 512 tokens ONLY IF your base chunks are short
  enough. Drop `max_chars` to ~1500 on the base chunker if you see truncation.
```

- [ ] **Step 5: Preview rendering**

```bash
grep -n "max_chars" docs/chunkers.md
```

Expected: at least 4 hits (two knob tables, tuning table, neighbor_expand warning).

- [ ] **Step 6: Commit**

```bash
git add docs/chunkers.md
git commit -m "docs(chunkers): document max_chars tuning + per-embedder recommendations (SC-007)"
```

## Task 8: Cross-brief annotation + release note

**Files:**
- Modify: `docs/superpowers/plans/2026-04-20-chunkshop-semantic-chunker.md`
- Modify: `README.md` (or current release-notes location)

- [ ] **Step 1: Annotate the semantic-chunker plan (SC-008)**

In `docs/superpowers/plans/2026-04-20-chunkshop-semantic-chunker.md`, find the reference to `max_chunk_chars: int = 3000`. Add a one-line note immediately after the config field definition:

```
> **Note (2026-04-21):** When Brief 3 is implemented, default `max_chunk_chars` should be `2000` to align with the hierarchy/sentence_aware hotfix (see `Mission-Brief-chunker-max-chars.md`). 3000 was specified before that hotfix landed; keeping 3000 would re-introduce the same silent-truncation bug on bge-small.
```

This is a single-line edit — do not restructure the plan.

- [ ] **Step 2: Add release-note line (SC-009)**

Identify the project's release-notes convention. Candidates in priority order:
1. `CHANGELOG.md` if it exists (check with `ls CHANGELOG.md`)
2. Top of `README.md` under a "Changes" / "Recent" section if present
3. `docs/README.md` under a release-notes heading

If none exist, create a short `CHANGELOG.md` at repo root:

```markdown
# Changelog

## Unreleased

### Fixed

- **Chunker `max_chars` hotfix.** `HierarchyChunker` previously emitted unbounded
  chunks between markdown headings; `SentenceAwareChunker` had a 3000-char cap
  (~750 tokens, over `bge-small-en-v1.5`'s 512-token limit). Both now enforce
  `max_chars: 2000` by default, splitting on paragraph→sentence→char boundaries.
  Split children of a single hierarchy section share `metadata.heading` and
  carry `metadata.section_part` (0-indexed). **Action:** Corpora previously
  ingested with oversized sections should be re-ingested; embeddings on
  oversized chunks only represented the first ~512 tokens. Users on larger-
  context embedders (`text-embedding-3-small/large`) should raise `max_chars`
  in YAML — see `docs/chunkers.md`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-04-20-chunkshop-semantic-chunker.md CHANGELOG.md
git commit -m "docs: CHANGELOG entry for chunker max_chars hotfix + semantic-chunker plan annotation (SC-008, SC-009)"
```

## ⛔ DC-FINAL Drift Check

Re-read mission brief. Evidence per SC:

- **SC-001:** `SentenceAwareChunker` config has `max_chars`, `min_chars`. Module constants gone. Covered by `test_sentence_aware_respects_configured_max_chars`.
- **SC-002:** `HierarchyChunker` config has `max_chars`. Oversized sections split. `metadata.heading` + `metadata.section_part` on children. Covered by `test_hierarchy_splits_oversized_sections` + `test_hierarchy_non_oversized_sections_still_work`.
- **SC-003:** `chunkshop/chunkers/_splitting.py` shared helper. Paragraph → sentence → char order. 6 tests in `test_splitting.py`.
- **SC-004:** `test_hierarchy_splits_oversized_sections`.
- **SC-005:** `test_sentence_aware_respects_configured_max_chars`.
- **SC-006:** Full regression — `uv run pytest -q` shows 70+ passing. No pre-existing test broken.
- **SC-007:** `docs/chunkers.md` has `max_chars` rows in both knob tables, a "Tuning for your embedder" table, and the neighbor_expand warning is updated.
- **SC-008:** `docs/superpowers/plans/2026-04-20-chunkshop-semantic-chunker.md` has a one-line annotation flagging the `max_chunk_chars` alignment.
- **SC-009:** `CHANGELOG.md` entry exists with behavior-change note + upgrade action.

**Verify:**

```bash
cd python && uv run pytest -q            # all green
git log --oneline main..HEAD              # ~5 commits on the branch
```

Expected branch commits (in order):
1. `feat(chunkers): shared paragraph->sentence->char splitter (SC-003)`
2. `feat(config): max_chars/min_chars fields on sentence_aware and hierarchy chunker configs (SC-001, SC-002 config)`
3. `feat(chunkers): sentence_aware uses cfg.max_chars instead of module constant (SC-001, SC-005)`
4. `feat(chunkers): hierarchy splits oversized sections, preserves heading + adds section_part (SC-002, SC-004)`
5. `docs(chunkers): document max_chars tuning + per-embedder recommendations (SC-007)`
6. `docs: CHANGELOG entry for chunker max_chars hotfix + semantic-chunker plan annotation (SC-008, SC-009)`

## Task 9: Merge to main

- [ ] **Step 1: Invoke finishing-a-development-branch skill**

Use `superpowers:finishing-a-development-branch`. Recommend fast-forward since the branch is linear ahead of main.

- [ ] **Step 2: After merge, clean up**

```bash
git worktree remove ../chunkshop-chunker-max-chars
git branch -d fix/chunker-max-chars   # if local branch still exists after worktree removal
```

- [ ] **Step 3: Do NOT push to origin**

User has accumulated commits ahead of `origin/main`. Leave the push for the user to batch.

## Notes for the executing agent

- **Worktree:** create `../chunkshop-chunker-max-chars -b fix/chunker-max-chars` before starting. Work in the worktree, not the main repo path.
- **Tests first:** every SC has a failing-test step BEFORE the implementation step. Don't skip this order — the whole point is the cap is enforced and we need red-then-green evidence.
- **Constants gone, not renamed:** `_MAX_CHARS` and `_MIN_CHARS` in `sentence_aware.py` should be removed entirely. Don't leave them as aliases. `self.cfg.max_chars` is the only source of truth.
- **Hierarchy `section_part: 0` on non-split sections:** this is deliberate — always present means downstream code doesn't need to handle "key may be absent." If tests assume absence, fix the tests.
- **Do NOT touch `fixed_overlap.py` or `neighbor_expand.py`.** Out of scope per the brief. `fixed_overlap` already has a word-bounded window; `neighbor_expand` is a wrapper.
- **Do NOT touch embedders or runner.** Bug is chunker-side only.
- **If DC-FINAL fails on any SC:** stop and report. Don't claim the work is done when a criterion lacks evidence.
