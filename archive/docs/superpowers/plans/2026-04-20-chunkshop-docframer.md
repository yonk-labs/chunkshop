# chunkshop DocFramer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable `DocFramer` protocol between Source and Chunker so messy real-world corpora (giant docs delimited by regex, nested JSON, heading-split dumps) become YAML config instead of bespoke loader functions.

**Architecture:** New `chunkshop/framers/` package with a `DocFramer` Protocol and four concrete implementations. New `framer:` section in `CellConfig` YAML (discriminated union). `runner.run_cell` nests `for doc in framer.frame(raw)` inside the existing source iteration. Default `IdentityFramer` preserves backward compatibility for every existing cell.

**Tech Stack:** Python 3.12, pydantic v2, re, json, pytest.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-docframer.md`. This plan implements all 13 Success Criteria. Drift Checkpoints (DC-001…DC-FINAL) injected as ⛔ hard gates.

---

## Prerequisites

- chunkshop checked out on `main` (or a feature branch rebased on main). This plan is independent of `feat/schema-flexibility`.
- `cd chunkshop/python && uv sync --extra dev` completed.
- Local Postgres+pgvector reachable for integration tests (or tests skip).

## File Structure

**New files:**
- `python/src/chunkshop/framers/__init__.py` — `load_framer` factory; re-exports.
- `python/src/chunkshop/framers/base.py` — `DocFramer` Protocol.
- `python/src/chunkshop/framers/identity.py` — `IdentityFramer`.
- `python/src/chunkshop/framers/heading_boundary.py` — `HeadingBoundaryFramer`.
- `python/src/chunkshop/framers/regex_boundary.py` — `RegexBoundaryFramer`.
- `python/src/chunkshop/framers/jsonpath.py` — `JSONPathFramer`.
- `python/tests/chunkshop/test_framers.py` — unit tests for all four framers.
- `python/tests/chunkshop/test_runner_framer.py` — runner integration test.
- `docs/tutorial-framers.md` — narrative walkthrough (SC-011).
- `docs/quickstart-framers.md` — YAML snippets + decision tree (SC-012).

**Modified files:**
- `python/src/chunkshop/config.py` — add `FramerConfig` discriminated union + four pydantic models + `framer` field on `CellConfig`.
- `python/src/chunkshop/runner.py` — nest framer iteration inside source loop; load framer via `load_framer`.

---

## Task 1: Context check

**Files:** (read-only)

- [ ] **Step 1: Verify baseline**

Run: `cd python && uv run pytest -q`
Expected: all tests pass. Note the count — this plan must preserve it.

- [ ] **Step 2: Note current Source/Chunker interfaces**

Read: `src/chunkshop/sources/base.py` (`Document` dataclass and `Source` Protocol), `src/chunkshop/chunkers/base.py` (`Chunk` + `Chunker` Protocol), `src/chunkshop/runner.py` (the `for doc in source.iter_documents()` loop).

## Task 2: DocFramer Protocol + IdentityFramer

**Files:**
- Create: `src/chunkshop/framers/__init__.py`
- Create: `src/chunkshop/framers/base.py`
- Create: `src/chunkshop/framers/identity.py`

- [ ] **Step 1: Write failing test**

Create `python/tests/chunkshop/test_framers.py`:

```python
from chunkshop.framers import IdentityFramer, load_framer
from chunkshop.framers.base import DocFramer
from chunkshop.sources.base import Document


def test_identity_framer_passes_through():
    framer = IdentityFramer()
    doc = Document(id="d1", content="hello world", title="t", metadata={"k": "v"})
    result = framer.frame(doc)
    assert len(result) == 1
    assert result[0].id == "d1"
    assert result[0].content == "hello world"
    assert result[0].metadata.get("framer") == "identity"
    assert result[0].metadata.get("frame_seq") == 0
    # Original metadata preserved
    assert result[0].metadata.get("k") == "v"


def test_identity_framer_satisfies_protocol():
    framer: DocFramer = IdentityFramer()
    assert hasattr(framer, "frame")
```

Run: `uv run pytest tests/chunkshop/test_framers.py -v` — FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Create `src/chunkshop/framers/base.py`**

```python
from __future__ import annotations
from typing import Protocol

from chunkshop.sources.base import Document


class DocFramer(Protocol):
    """Split one raw Document from a Source into one-or-more framed Documents.

    Implementations should add ``metadata["framer"]`` (framer name) and
    ``metadata["frame_seq"]`` (0-indexed position within raw doc) to each framed
    output. Raw doc metadata is preserved by value copy.

    Stateless: no I/O, no resource handles. A DocFramer is a pure function.
    """
    def frame(self, raw: Document) -> list[Document]: ...
```

- [ ] **Step 3: Create `src/chunkshop/framers/identity.py`**

```python
from __future__ import annotations
from dataclasses import replace

from chunkshop.sources.base import Document


class IdentityFramer:
    """Default framer: 1-to-1 pass-through. Tags the doc with framer='identity'."""

    def frame(self, raw: Document) -> list[Document]:
        meta = dict(raw.metadata or {})
        meta["framer"] = "identity"
        meta["frame_seq"] = 0
        return [replace(raw, metadata=meta)]
```

- [ ] **Step 4: Create `src/chunkshop/framers/__init__.py`**

```python
from chunkshop.framers.base import DocFramer
from chunkshop.framers.identity import IdentityFramer


def load_framer(cfg) -> DocFramer:
    """Factory. Dispatches on the config model's type literal.

    Called from runner.py. Accepts any FramerConfig variant; returns a concrete DocFramer.
    """
    # Discriminator dispatch expands as each framer type is added in later tasks.
    from chunkshop.config import (
        IdentityFramerConfig,
    )

    if isinstance(cfg, IdentityFramerConfig):
        return IdentityFramer()
    raise ValueError(f"unknown framer type: {type(cfg).__name__}")


__all__ = ["DocFramer", "IdentityFramer", "load_framer"]
```

- [ ] **Step 5: Add `IdentityFramerConfig` to `config.py`**

In `src/chunkshop/config.py`, after the `ChunkerConfig` union definition and before `FastembedEmbedder`, add:

```python
class IdentityFramerConfig(_Base):
    type: Literal["identity"] = "identity"


FramerConfig = Annotated[
    IdentityFramerConfig,  # expand to Union in Task 4
    Field(discriminator="type"),
]
```

Then in `CellConfig`, add the new field AFTER `source`:

```python
class CellConfig(_Base):
    cell_name: str
    source: SourceConfig
    framer: FramerConfig = Field(default_factory=lambda: IdentityFramerConfig())
    chunker: ChunkerConfig
    ...
```

- [ ] **Step 6: Run test — expect PASS**

`uv run pytest tests/chunkshop/test_framers.py -v`

- [ ] **Step 7: Commit**

```bash
git add python/src/chunkshop/framers/ python/src/chunkshop/config.py python/tests/chunkshop/test_framers.py
git commit -m "feat(framers): DocFramer Protocol + IdentityFramer default"
```

## Task 3: Runner integration

**Files:**
- Modify: `src/chunkshop/runner.py`
- Create: `python/tests/chunkshop/test_runner_framer.py`

- [ ] **Step 1: Write failing test**

Create `python/tests/chunkshop/test_runner_framer.py`:

```python
"""Regression: runner still emits one chunk per raw doc when framer=identity (default)."""
import json
import os
import pytest
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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_framer CASCADE")
        conn.commit()


def test_identity_framer_default_preserves_existing_behavior(ensure_pg, tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({
        "documents": [
            {"id": "d1", "title": "T1",
             "content": "# Alpha\n\nAlpha bravo charlie delta echo foxtrot golf."},
        ]
    }))

    cfg = CellConfig(
        cell_name="framer_default",
        source={"type": "json_corpus", "path": str(corpus)},
        # No framer section — default IdentityFramer applies.
        chunker={"type": "hierarchy"},
        embedder={"type": "fastembed",
                  "model_name": "Xenova/bge-small-en-v1.5-int8",
                  "dim": 384, "threads": 2},
        target={"dsn_env": DSN_ENV, "schema": "chunkshop_test_framer",
                "table": "t", "overwrite": True, "hnsw": False},
    )

    result = run_cell(cfg)
    assert result.error is None, result.error
    assert result.docs_processed == 1
    assert result.chunks_written >= 1
```

Run: expect FAIL — runner doesn't call framer yet.

- [ ] **Step 2: Modify `src/chunkshop/runner.py`**

Add import at the top alongside other module imports:

```python
from chunkshop.framers import load_framer
```

Inside `run_cell`, after `source = load_source(cfg.source)`, add:

```python
framer = load_framer(cfg.framer)
```

Then change the iteration loop. The current block (roughly):

```python
for doc in source.iter_documents():
    if limit is not None and docs_processed >= limit:
        break
    chunks = chunker.chunk(doc)
    ...
```

Becomes:

```python
for raw in source.iter_documents():
    for doc in framer.frame(raw):
        if limit is not None and docs_processed >= limit:
            break
        chunks = chunker.chunk(doc)
        if not chunks:
            docs_processed += 1
            continue
        texts = [c.embedded_content for c in chunks]
        embeddings = embedder.embed(texts)
        results = [extractor.extract(c.original_content) for c in chunks]
        tags = [r.tags for r in results]
        chunks = [
            _replace(c, metadata={**r.metadata, **c.metadata})
            for c, r in zip(chunks, results)
        ]
        sink.write_document(doc.id, chunks, embeddings, tags)
        chunks_written += len(chunks)
        docs_processed += 1
        if docs_processed % heartbeat == 0:
            elapsed = time.time() - start
            _log(
                f"heartbeat docs={docs_processed} chunks={chunks_written} elapsed={elapsed:.1f}s",
                log_path,
            )
    if limit is not None and docs_processed >= limit:
        break
```

Note the outer `break` after the inner loop — handles the case where `doc_limit` fires mid-frame-batch.

- [ ] **Step 3: Re-run — expect PASS**

`uv run pytest tests/chunkshop/test_runner_framer.py -v`

- [ ] **Step 4: Full-suite regression**

`uv run pytest -q` — expect no regressions.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/runner.py python/tests/chunkshop/test_runner_framer.py
git commit -m "feat(runner): nest framer.frame inside source loop (identity default)"
```

## ⛔ DC-001 Drift Check: Framer Protocol + Identity + runner integration

**Re-read:** `skill-output/mission-brief/Mission-Brief-docframer.md`. Verify SC-001, SC-002, SC-008.

**Gate:**
- [ ] `uv run pytest -q` — all tests pass.
- [ ] Existing cells with no `framer:` in YAML still produce identical chunk counts.

## Task 4: HeadingBoundaryFramer

**Files:**
- Create: `src/chunkshop/framers/heading_boundary.py`
- Modify: `src/chunkshop/config.py`
- Modify: `src/chunkshop/framers/__init__.py`
- Modify: `python/tests/chunkshop/test_framers.py`

- [ ] **Step 1: Add tests**

Append to `test_framers.py`:

```python
from chunkshop.framers import HeadingBoundaryFramer
from chunkshop.config import HeadingBoundaryFramerConfig


def test_heading_boundary_splits_on_h2():
    raw = Document(
        id="d1",
        content="# Title\n\nIntro.\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.",
        title="Doc",
        metadata={},
    )
    framer = HeadingBoundaryFramer(HeadingBoundaryFramerConfig(pattern=r"^##\s"))
    out = framer.frame(raw)
    assert len(out) == 2
    assert out[0].title == "Section A"
    assert "Body A." in out[0].content
    assert out[1].title == "Section B"
    assert "Body B." in out[1].content
    for i, d in enumerate(out):
        assert d.metadata["framer"] == "heading_boundary"
        assert d.metadata["frame_seq"] == i


def test_heading_boundary_no_headings_returns_single_frame():
    raw = Document(id="d1", content="No headings here at all.", title="t", metadata={})
    framer = HeadingBoundaryFramer(HeadingBoundaryFramerConfig())
    out = framer.frame(raw)
    assert len(out) == 1
    assert out[0].content == "No headings here at all."


def test_heading_boundary_preserves_preamble():
    raw = Document(
        id="d1",
        content="Preamble before any heading.\n\n# H1\n\nBody.",
        title="t", metadata={},
    )
    framer = HeadingBoundaryFramer(HeadingBoundaryFramerConfig(pattern=r"^#\s"))
    out = framer.frame(raw)
    # Preamble is either its own frame or prepended to the first heading's frame.
    # Implementation choice: emit preamble as frame_seq=0 if it has content.
    assert len(out) == 2
    assert "Preamble" in out[0].content
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `HeadingBoundaryFramer`**

Create `src/chunkshop/framers/heading_boundary.py`:

```python
from __future__ import annotations
import re
from dataclasses import replace

from chunkshop.config import HeadingBoundaryFramerConfig
from chunkshop.sources.base import Document


class HeadingBoundaryFramer:
    """Split a Document on a markdown heading pattern.

    Each framed doc's ``title`` is the heading text (when ``title_from_heading=True``).
    Pre-heading preamble is emitted as frame 0 if non-empty.
    """

    def __init__(self, cfg: HeadingBoundaryFramerConfig):
        self.cfg = cfg
        self._pattern = re.compile(cfg.pattern + r".+$", re.MULTILINE)

    def frame(self, raw: Document) -> list[Document]:
        content = raw.content
        matches = list(self._pattern.finditer(content))
        if not matches:
            meta = dict(raw.metadata or {})
            meta["framer"] = "heading_boundary"
            meta["frame_seq"] = 0
            return [replace(raw, metadata=meta)]

        frames: list[Document] = []
        # Preamble
        if matches[0].start() > 0:
            preamble = content[: matches[0].start()].strip()
            if preamble:
                meta = dict(raw.metadata or {})
                meta["framer"] = "heading_boundary"
                meta["frame_seq"] = 0
                frames.append(replace(raw, id=f"{raw.id}#0", content=preamble, metadata=meta))
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            heading_line = m.group(0).strip()
            # Strip the leading pattern to get the heading text
            heading_text = re.sub(self.cfg.pattern, "", heading_line).strip()
            body = content[start:end].strip()
            full = f"{heading_line}\n\n{body}" if body else heading_line
            meta = dict(raw.metadata or {})
            meta["framer"] = "heading_boundary"
            meta["frame_seq"] = len(frames)
            frames.append(replace(
                raw,
                id=f"{raw.id}#{len(frames)}",
                title=heading_text if self.cfg.title_from_heading else raw.title,
                content=full,
                metadata=meta,
            ))
        return frames
```

- [ ] **Step 4: Add `HeadingBoundaryFramerConfig` to `config.py`**

Next to `IdentityFramerConfig`:

```python
class HeadingBoundaryFramerConfig(_Base):
    type: Literal["heading_boundary"] = "heading_boundary"
    pattern: str = r"^#+\s"
    title_from_heading: bool = True
```

Update the `FramerConfig` union:

```python
FramerConfig = Annotated[
    Union[IdentityFramerConfig, HeadingBoundaryFramerConfig],
    Field(discriminator="type"),
]
```

- [ ] **Step 5: Update `load_framer` factory**

In `src/chunkshop/framers/__init__.py`:

```python
from chunkshop.framers.base import DocFramer
from chunkshop.framers.identity import IdentityFramer
from chunkshop.framers.heading_boundary import HeadingBoundaryFramer


def load_framer(cfg) -> DocFramer:
    from chunkshop.config import (
        IdentityFramerConfig,
        HeadingBoundaryFramerConfig,
    )

    if isinstance(cfg, IdentityFramerConfig):
        return IdentityFramer()
    if isinstance(cfg, HeadingBoundaryFramerConfig):
        return HeadingBoundaryFramer(cfg)
    raise ValueError(f"unknown framer type: {type(cfg).__name__}")


__all__ = ["DocFramer", "IdentityFramer", "HeadingBoundaryFramer", "load_framer"]
```

- [ ] **Step 6: Re-run — expect PASS**

- [ ] **Step 7: Commit**

```bash
git add python/src/chunkshop/framers/heading_boundary.py python/src/chunkshop/framers/__init__.py python/src/chunkshop/config.py python/tests/chunkshop/test_framers.py
git commit -m "feat(framers): HeadingBoundaryFramer — split on markdown heading pattern"
```

## ⛔ DC-002 Drift Check: HeadingBoundaryFramer

**Re-read brief SC-003.** Verify heading fixture produces expected frames, including preamble preservation.

## Task 5: RegexBoundaryFramer

**Files:**
- Create: `src/chunkshop/framers/regex_boundary.py`
- Modify: `src/chunkshop/config.py`
- Modify: `src/chunkshop/framers/__init__.py`
- Modify: `python/tests/chunkshop/test_framers.py`

- [ ] **Step 1: Add tests**

Append to `test_framers.py`:

```python
from chunkshop.framers import RegexBoundaryFramer
from chunkshop.config import RegexBoundaryFramerConfig


def test_regex_boundary_medical_topic_split():
    """Simulates the pg-raggraph medical corpus pattern: 'About <topic>' separators."""
    content = (
        "About Lupus. Lupus is an autoimmune disease. It affects joints. "
        "About Diabetes. Diabetes is a metabolic disorder. Insulin management matters. "
        "About Asthma. Asthma narrows airways. Triggers vary."
    )
    raw = Document(id="med", content=content, title="Medical", metadata={})
    framer = RegexBoundaryFramer(RegexBoundaryFramerConfig(
        split_pattern=r"(?:^|(?<=[.?!]\s))About\s+",
        title_pattern=r"About\s+([^.?]{3,80})",
    ))
    out = framer.frame(raw)
    assert len(out) == 3
    titles = {d.title for d in out}
    assert "Lupus" in titles
    assert "Diabetes" in titles
    assert "Asthma" in titles
    for i, d in enumerate(out):
        assert d.metadata["framer"] == "regex_boundary"
        assert d.metadata["frame_seq"] == i


def test_regex_boundary_no_match_returns_single_frame():
    raw = Document(id="d1", content="No boundaries here.", title="t", metadata={})
    framer = RegexBoundaryFramer(RegexBoundaryFramerConfig(split_pattern=r"SPLIT"))
    out = framer.frame(raw)
    assert len(out) == 1
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

Create `src/chunkshop/framers/regex_boundary.py`:

```python
from __future__ import annotations
import re
from dataclasses import replace

from chunkshop.config import RegexBoundaryFramerConfig
from chunkshop.sources.base import Document


class RegexBoundaryFramer:
    """Split a Document's content on a regex boundary.

    Each slice becomes one framed Document. When ``title_pattern`` is provided,
    the first capture group from matching each slice becomes the framed title.
    """

    def __init__(self, cfg: RegexBoundaryFramerConfig):
        self.cfg = cfg
        # Validate at init time — fail fast on bad patterns.
        self._split_re = re.compile(cfg.split_pattern, re.MULTILINE)
        self._title_re = re.compile(cfg.title_pattern) if cfg.title_pattern else None

    def frame(self, raw: Document) -> list[Document]:
        content = raw.content
        matches = list(self._split_re.finditer(content))
        if not matches:
            meta = dict(raw.metadata or {})
            meta["framer"] = "regex_boundary"
            meta["frame_seq"] = 0
            return [replace(raw, metadata=meta)]

        frames: list[Document] = []
        for i, m in enumerate(matches):
            start = m.start() if self.cfg.body_starts_with_match else m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[start:end].strip()
            if not body:
                continue
            title = raw.title
            if self._title_re:
                tm = self._title_re.search(body)
                if tm and tm.groups():
                    title = tm.group(1).strip()
            meta = dict(raw.metadata or {})
            meta["framer"] = "regex_boundary"
            meta["frame_seq"] = len(frames)
            frames.append(replace(
                raw,
                id=f"{raw.id}#{len(frames)}",
                content=body,
                title=title,
                metadata=meta,
            ))
        return frames
```

- [ ] **Step 4: Add `RegexBoundaryFramerConfig`**

In `config.py`:

```python
class RegexBoundaryFramerConfig(_Base):
    type: Literal["regex_boundary"] = "regex_boundary"
    split_pattern: str
    title_pattern: Optional[str] = None
    body_starts_with_match: bool = True

    @field_validator("split_pattern", "title_pattern")
    @classmethod
    def _valid_regex(cls, v):
        if v is None:
            return v
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}")
        return v
```

Update `FramerConfig` union to include `RegexBoundaryFramerConfig`.

- [ ] **Step 5: Update `load_framer` factory** to dispatch on `RegexBoundaryFramerConfig`.

- [ ] **Step 6: Re-run — expect PASS**

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(framers): RegexBoundaryFramer — split on arbitrary regex with optional title extraction"
```

## ⛔ DC-003 Drift Check: RegexBoundaryFramer

Verify the medical-topic pattern from pg-raggraph reproduces correctly. The test `test_regex_boundary_medical_topic_split` is the canonical reference.

## Task 6: JSONPathFramer

**Files:**
- Create: `src/chunkshop/framers/jsonpath.py`
- Modify: `src/chunkshop/config.py`
- Modify: `src/chunkshop/framers/__init__.py`
- Modify: `python/tests/chunkshop/test_framers.py`

- [ ] **Step 1: Add tests**

Append:

```python
import json as _json
from chunkshop.framers import JSONPathFramer
from chunkshop.config import JSONPathFramerConfig


def test_jsonpath_list_expansion():
    """Raw doc's content is a JSON blob; expand items[*] into framed docs."""
    payload = {
        "meta": {"source": "api"},
        "items": [
            {"id": "a", "body": "first doc body"},
            {"id": "b", "body": "second doc body"},
            {"id": "c", "body": "third doc body"},
        ],
    }
    raw = Document(id="bundle", content=_json.dumps(payload), title="Bundle", metadata={})
    framer = JSONPathFramer(JSONPathFramerConfig(
        row_path="items.*",
        title_path="id",
        body_path="body",
    ))
    out = framer.frame(raw)
    assert len(out) == 3
    assert out[0].title == "a"
    assert "first doc body" in out[0].content
    for i, d in enumerate(out):
        assert d.metadata["framer"] == "jsonpath"
        assert d.metadata["frame_seq"] == i


def test_jsonpath_missing_row_path_returns_empty():
    raw = Document(id="bundle", content='{"other": []}', title="t", metadata={})
    framer = JSONPathFramer(JSONPathFramerConfig(row_path="items.*", body_path="body"))
    out = framer.frame(raw)
    assert out == []


def test_jsonpath_invalid_json_raises():
    raw = Document(id="bundle", content="not-json-at-all", title="t", metadata={})
    framer = JSONPathFramer(JSONPathFramerConfig(row_path="items.*", body_path="body"))
    import pytest
    with pytest.raises(ValueError, match="JSON"):
        framer.frame(raw)
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

Create `src/chunkshop/framers/jsonpath.py`:

```python
from __future__ import annotations
import json
from dataclasses import replace

from chunkshop.config import JSONPathFramerConfig
from chunkshop.sources.base import Document


def _walk(obj, path_parts: list[str]) -> list:
    """Traverse dotted path with '*' for list iteration. Returns list of values."""
    if not path_parts:
        return [obj]
    head, *rest = path_parts
    if head == "*":
        if not isinstance(obj, list):
            return []
        out = []
        for item in obj:
            out.extend(_walk(item, rest))
        return out
    if isinstance(obj, dict) and head in obj:
        return _walk(obj[head], rest)
    return []


class JSONPathFramer:
    """Parse raw.content as JSON, walk a dotted path (with '*' for list iteration),
    and emit one framed Document per element.
    """

    def __init__(self, cfg: JSONPathFramerConfig):
        self.cfg = cfg
        self._row_parts = cfg.row_path.split(".")
        self._body_parts = cfg.body_path.split(".") if cfg.body_path != "$" else []
        self._title_parts = cfg.title_path.split(".") if cfg.title_path else None

    def frame(self, raw: Document) -> list[Document]:
        try:
            obj = json.loads(raw.content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONPathFramer: raw.content is not valid JSON: {exc}")
        rows = _walk(obj, self._row_parts)
        frames: list[Document] = []
        for i, row in enumerate(rows):
            body_values = _walk(row, self._body_parts) if self._body_parts else [row]
            if not body_values:
                continue
            body = body_values[0]
            if not isinstance(body, str):
                body = json.dumps(body)
            title = raw.title
            if self._title_parts:
                tvs = _walk(row, self._title_parts)
                if tvs and isinstance(tvs[0], str):
                    title = tvs[0]
            meta = dict(raw.metadata or {})
            meta["framer"] = "jsonpath"
            meta["frame_seq"] = len(frames)
            frames.append(replace(
                raw,
                id=f"{raw.id}#{len(frames)}",
                content=body,
                title=title,
                metadata=meta,
            ))
        return frames
```

- [ ] **Step 4: Add `JSONPathFramerConfig`** in `config.py` with path-shape validators (reject characters outside `^[a-z_0-9.*]+$` per segment). Include in union.

- [ ] **Step 5: Update `load_framer`** to dispatch on `JSONPathFramerConfig`.

- [ ] **Step 6: Re-run — expect PASS**

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(framers): JSONPathFramer — expand nested JSON array into framed docs"
```

## ⛔ DC-004 Drift Check: JSONPathFramer

Verify dotted paths with `*` list iteration work on nested and flat structures.

## Task 7: Metadata safety invariants

Every framer's output should carry forward raw metadata BY VALUE. Verify no framer accidentally shares a metadata dict reference across frames.

- [ ] **Step 1: Add regression test**

Append to `test_framers.py`:

```python
def test_framers_do_not_share_metadata_references():
    """Mutating one framed doc's metadata must not bleed to siblings."""
    raw = Document(id="d1", content="# A\n\nbody A.\n\n# B\n\nbody B.",
                   title="t", metadata={"keep": "me"})
    framer = HeadingBoundaryFramer(HeadingBoundaryFramerConfig(pattern=r"^#\s"))
    frames = framer.frame(raw)
    assert len(frames) >= 2
    frames[0].metadata["mutation"] = "test"
    assert "mutation" not in frames[1].metadata
    assert "mutation" not in raw.metadata  # parent also untouched
```

- [ ] **Step 2: Run** — expect PASS (all framers already use `dict(raw.metadata or {})`).

If it fails for any framer, that framer is leaking references — fix it.

- [ ] **Step 3: Commit**

```bash
git commit -m "test(framers): pin by-value metadata isolation across frames"
```

## ⛔ DC-005 Drift Check: Pre-docs gate

Re-read `skill-output/mission-brief/Mission-Brief-docframer.md`. Every code SC (SC-001…SC-010, SC-013) has evidence. Full suite passes.

## Task 8: Tutorial

**Files:**
- Create: `docs/tutorial-framers.md`

- [ ] **Step 1: Write tutorial**

Write `docs/tutorial-framers.md` covering two realistic scenarios:

1. A giant markdown file with H2-delimited topics → `HeadingBoundaryFramer(pattern=r"^##\s")`.
2. A nested JSON API dump where docs live at `items[*].body` → `JSONPathFramer`.

For each scenario: YAML config + `chunkshop ingest` command + SQL check. Include a "what this replaces" note: "Before DocFramer, you'd write a 30-line splitter function in your ingest code. With framer config, it's 4 lines of YAML."

- [ ] **Step 2: Commit**

```bash
git add docs/tutorial-framers.md
git commit -m "docs: tutorial for DocFramer — markdown heading and JSON-nested scenarios (SC-011)"
```

## Task 9: Quickstart

**Files:**
- Create: `docs/quickstart-framers.md`

- [ ] **Step 1: Write quickstart**

```markdown
# Quickstart: DocFramer

Choose a framer for your source's shape.

## Decision tree

| Your source gives you… | Use |
|---|---|
| One doc per row, already split correctly | Nothing — default `IdentityFramer`. |
| Markdown with `##` sections as logical docs | `heading_boundary` with `pattern: ^##\s` |
| Plain text with a custom separator | `regex_boundary` with your pattern |
| JSON with docs nested under `$.items[*]` | `jsonpath` with `row_path: items.*` |

## YAML recipes

### HeadingBoundary

```yaml
framer:
  type: heading_boundary
  pattern: '^##\s'
  title_from_heading: true
```

### RegexBoundary

```yaml
framer:
  type: regex_boundary
  split_pattern: '(?:^|(?<=[.?!]\s))About\s+'
  title_pattern: 'About\s+([^.?]{3,80})'
```

### JSONPath

```yaml
framer:
  type: jsonpath
  row_path: items.*
  title_path: id
  body_path: body
```

## What it replaces

Before DocFramer, this required bespoke code per corpus:

```python
# BEFORE — custom splitter in your ingest script
def split_medical_topics(text):
    parts = re.split(r"(?:^|(?<=[.?!]\s))About\s+", text)
    return [Document(id=f"med_{i}", content=p) for i, p in enumerate(parts)]
```

After:

```yaml
# AFTER — 4 lines of config, same behavior
framer:
  type: regex_boundary
  split_pattern: '(?:^|(?<=[.?!]\s))About\s+'
  title_pattern: 'About\s+([^.?]{3,80})'
```

Full tutorial: [`tutorial-framers.md`](tutorial-framers.md).
```

- [ ] **Step 2: Commit**

```bash
git commit -m "docs: quickstart for DocFramer with decision tree + recipes (SC-012)"
```

## ⛔ DC-FINAL Drift Check

Re-read mission brief. Evidence per SC:

- SC-001: `test_identity_framer_satisfies_protocol`.
- SC-002: `test_identity_framer_passes_through`.
- SC-003: `test_heading_boundary_*`.
- SC-004: `test_regex_boundary_*`.
- SC-005: `test_jsonpath_*`.
- SC-006: Pydantic model tests (add one if not covered by default-field checks).
- SC-007: `load_framer` factory tested implicitly by framer tests through its dispatch.
- SC-008: `test_identity_framer_default_preserves_existing_behavior` in `test_runner_framer.py`.
- SC-009: Full-suite regression.
- SC-010: `test_framers_do_not_share_metadata_references` + per-framer tests asserting `meta["framer"]` and `meta["frame_seq"]`.
- SC-011: `docs/tutorial-framers.md`.
- SC-012: `docs/quickstart-framers.md`.
- SC-013: Count of tests per framer — each has happy/edge/degenerate cases.

**Verify:**

```bash
cd python && uv run pytest -q          # no regressions
git log --oneline main..HEAD           # ~8 commits on the branch
```

## Notes for the executing agent

- **Worktree:** create `../chunkshop-docframer -b feat/docframer` before starting.
- **Independence:** This plan does NOT depend on `feat/schema-flexibility`. It can be worked in parallel.
- **Runner touches:** only one file (`runner.py`). If both this plan and schema-flex are in flight, coordinate the runner merge order.
- **File-size contribution:** `framers/` package is new. Keep each framer file under 80 lines. Config additions keep `config.py` linear.

## Follow-ups (NOT this plan)

- A real JSONPath expression engine (if `items.*` dotted form proves too limited).
- Cross-doc framers (take multiple raw docs, emit one framed output — e.g., "merge consecutive 1-paragraph docs into sections").
- Framer-aware metrics in the runner (per-framer avg frames/doc, framer-miss count).
