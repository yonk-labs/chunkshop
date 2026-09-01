# Media Ingest v1-A — Deriver Seam, Provenance Spine, Keyless Images — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a folder of images searchable through chunkshop's existing text pipeline with no API key, no GPU, and no extra installed — and build the provenance/caching spine every later modality reuses.

**Architecture:** Generalise the existing `FileParser` into a `Deriver` that may return metadata alongside text, *without breaking source compatibility*. Wrap every deriver in a provenance-keyed memo cache. Add two keyless image rungs (Pillow metadata, CLIP zero-shot tagging) whose output is ordinary text landing in the ordinary text table — so the shipped hybrid search needs no changes at all.

**Tech Stack:** Python 3.12, pydantic v2 (`extra="forbid"`), pytest, `fastembed` 0.8 (already a core dep — brings `onnxruntime` and `Pillow` transitively).

**Spec:** `spec/chunkshop-media-ingest/SPEC.md` (all 12 phases confirmed). Milestones M0–M3 of `spec/chunkshop-media-ingest/PLAN.md`.

## Global Constraints

- **No new entry in `[project] dependencies`.** Media libs go in extras only. (NN-6, SC-13)
- **No model download at install or import.** Weights load lazily on first *use*. (NN-6)
- **No breaking change.** A third-party parser written as `def parse(self, path) -> str` must keep working and must not require edits. (F1, SC-13)
- **Existing text YAMLs must produce byte-identical output.** (SC-13)
- **Nothing derived is written without provenance:** model id, version, timestamp, params hash. (NN-3, SC-8)
- **Empty derivation is a recorded failure, never a silent empty document.** (F5, SC-5)
- **Config is pydantic with `extra="forbid"`** — every new config object inherits `_Base`.
- **Identifier safety:** any new promoted column name must satisfy `^[a-z_][a-z0-9_]*$`.
- Python target: 3.12. Test command: `uv run --no-sync pytest -q` from `python/`.

---

## File Structure

**Create:**
- `python/src/chunkshop/sources/parsers/result.py` — `ParseResult`, `normalize_parse_result()`
- `python/src/chunkshop/sources/derivers/__init__.py` — deriver registry + `load_deriver()`
- `python/src/chunkshop/sources/derivers/image_meta.py` — rung 0, Pillow metadata
- `python/src/chunkshop/sources/derivers/clip_tags.py` — rung 1, CLIP zero-shot tagging
- `python/src/chunkshop/sources/derivers/cache.py` — provenance-keyed memo cache
- `python/src/chunkshop/sources/derivers/provenance.py` — derivation-hash + stamping

**Modify:**
- `python/src/chunkshop/sources/parsers/base.py` — add `Deriver` protocol, keep `FileParser` alias
- `python/src/chunkshop/sources/parsers/pdf.py` — stop swallowing empty extractions
- `python/src/chunkshop/sources/files.py:122-136` — normalize results, merge metadata, apply derive config
- `python/src/chunkshop/config.py` — `FilesSource.derive`, `FilesSource.on_empty`, deriver config union
- `python/pyproject.toml` — extras (`vision`, `ocr`, `audio`, `media`)

---

### Task 1: `ParseResult` and the widened `Deriver` protocol

Backward-compatible generalisation. `FileParser` stays exported and working.

**Files:**
- Create: `python/src/chunkshop/sources/parsers/result.py`
- Modify: `python/src/chunkshop/sources/parsers/base.py`
- Modify: `python/src/chunkshop/sources/parsers/__init__.py`
- Test: `python/tests/chunkshop/test_parse_result.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `ParseResult(text: str, metadata: dict)`; `normalize_parse_result(value: str | ParseResult) -> ParseResult`; `Deriver` Protocol with `derive(path: Path) -> str | ParseResult`; `FileParser` remains an alias whose `parse` is still honoured.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_parse_result.py`:

```python
from pathlib import Path

import pytest

from chunkshop.sources.parsers.result import ParseResult, normalize_parse_result


def test_parse_result_holds_text_and_metadata():
    r = ParseResult(text="hello", metadata={"width": 8})
    assert r.text == "hello"
    assert r.metadata == {"width": 8}


def test_parse_result_metadata_defaults_to_empty_dict():
    assert ParseResult(text="hi").metadata == {}


def test_normalize_wraps_a_bare_string():
    """Old-style parsers return str. They must keep working."""
    r = normalize_parse_result("legacy output")
    assert isinstance(r, ParseResult)
    assert r.text == "legacy output"
    assert r.metadata == {}


def test_normalize_passes_through_a_parse_result():
    original = ParseResult(text="x", metadata={"a": 1})
    assert normalize_parse_result(original) is original


def test_normalize_rejects_other_types():
    with pytest.raises(TypeError, match="str or ParseResult"):
        normalize_parse_result(42)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_parse_result.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunkshop.sources.parsers.result'`

- [ ] **Step 3: Write minimal implementation**

Create `python/src/chunkshop/sources/parsers/result.py`:

```python
"""The value a deriver returns: extracted text plus whatever metadata the
derivation produced (dimensions, duration, model provenance, ...).

Derivers may return a bare ``str`` — the historical ``FileParser`` contract —
and ``normalize_parse_result`` wraps it. This is what keeps third-party
parsers written against chunkshop 1.0 working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParseResult:
    text: str
    metadata: dict = field(default_factory=dict)


def normalize_parse_result(value: "str | ParseResult") -> ParseResult:
    """Coerce a deriver's return value to a ParseResult.

    A bare ``str`` is the legacy ``FileParser.parse`` contract and is wrapped
    with empty metadata.
    """
    if isinstance(value, ParseResult):
        return value
    if isinstance(value, str):
        return ParseResult(text=value)
    raise TypeError(
        f"deriver returned {type(value).__name__}; expected str or ParseResult"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_parse_result.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Add the `Deriver` protocol beside `FileParser`**

Append to `python/src/chunkshop/sources/parsers/base.py` (keep everything already there — `ParserError` and `FileParser` are unchanged):

```python
from chunkshop.sources.parsers.result import ParseResult  # noqa: E402


class EmptyDerivationError(ParserError):
    """A file was recognised and read, but produced no text.

    Raised instead of silently indexing an empty document — a scanned PDF
    with no embedded text is a derivation failure, not a zero-length success.
    """


@runtime_checkable
class Deriver(Protocol):
    """Generalisation of FileParser: may return metadata alongside text.

    ``FileParser`` remains valid and supported — a deriver may implement
    either ``derive`` or the legacy ``parse``, and either may return a bare
    ``str``. ``call_deriver`` picks whichever exists.
    """

    supported_extensions: list[str]

    def derive(self, path: "Path") -> "str | ParseResult": ...


def call_deriver(obj, path: "Path") -> ParseResult:
    """Invoke a deriver or a legacy parser and normalise its result."""
    fn = getattr(obj, "derive", None) or getattr(obj, "parse", None)
    if fn is None:
        raise TypeError(
            f"{type(obj).__name__} has neither .derive() nor .parse()"
        )
    return normalize_parse_result(fn(path))
```

Add to the imports at the top of that file:

```python
from chunkshop.sources.parsers.result import ParseResult, normalize_parse_result
```

- [ ] **Step 6: Write the backward-compatibility test**

Append to `python/tests/chunkshop/test_parse_result.py`:

```python
from chunkshop.sources.parsers.base import Deriver, call_deriver


def test_legacy_parse_only_object_still_works(tmp_path):
    """A parser written against chunkshop 1.0 must work with NO edits."""

    class LegacyParser:
        supported_extensions = ["weird"]

        def parse(self, path: Path) -> str:
            return path.read_text().upper()

    p = tmp_path / "a.weird"
    p.write_text("raw", encoding="utf-8")
    result = call_deriver(LegacyParser(), p)
    assert result.text == "RAW"
    assert result.metadata == {}


def test_new_style_deriver_returns_metadata(tmp_path):
    class MetaDeriver:
        supported_extensions = ["weird"]

        def derive(self, path: Path) -> ParseResult:
            return ParseResult(text="body", metadata={"kind": "weird"})

    p = tmp_path / "a.weird"
    p.write_text("x", encoding="utf-8")
    result = call_deriver(MetaDeriver(), p)
    assert result.text == "body"
    assert result.metadata == {"kind": "weird"}


def test_object_with_neither_method_is_rejected(tmp_path):
    class Broken:
        supported_extensions = ["weird"]

    with pytest.raises(TypeError, match="neither"):
        call_deriver(Broken(), tmp_path / "a.weird")
```

- [ ] **Step 7: Run the full parser suite**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_parse_result.py tests/chunkshop/test_parser_base.py tests/chunkshop/test_parser_registry.py tests/chunkshop/test_files_dispatch.py -q`
Expected: PASS. `test_files_dispatch.py::test_custom_parser_injection` — which defines a `str`-returning parser — must still pass **unmodified**. That test is the source-compatibility guarantee.

- [ ] **Step 8: Export the new names**

In `python/src/chunkshop/sources/parsers/__init__.py`, add to the imports and to `__all__`:

```python
from chunkshop.sources.parsers.base import (
    Deriver,
    EmptyDerivationError,
    FileParser,
    ParserError,
    call_deriver,
)
from chunkshop.sources.parsers.result import ParseResult, normalize_parse_result
```

Add `"Deriver"`, `"EmptyDerivationError"`, `"call_deriver"`, `"ParseResult"`, `"normalize_parse_result"` to `__all__`.

- [ ] **Step 9: Commit**

```bash
git add python/src/chunkshop/sources/parsers/result.py \
        python/src/chunkshop/sources/parsers/base.py \
        python/src/chunkshop/sources/parsers/__init__.py \
        python/tests/chunkshop/test_parse_result.py
git commit -m "feat(parsers): add ParseResult and backward-compatible Deriver protocol"
```

---

### Task 2: `FilesSource` consumes `ParseResult` and merges its metadata

**Files:**
- Modify: `python/src/chunkshop/sources/files.py:122-136` (`_document_for`)
- Test: `python/tests/chunkshop/test_files_deriver_metadata.py`

**Interfaces:**
- Consumes: `call_deriver` from Task 1.
- Produces: `Document.metadata` now contains deriver-emitted keys merged **under** the source's own `source_path`/`parser` keys (source-wins, mirroring the runner's chunker-wins rule).

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_files_deriver_metadata.py`:

```python
from pathlib import Path

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers.result import ParseResult


class _MetaDeriver:
    supported_extensions = ["weird"]

    def derive(self, path: Path) -> ParseResult:
        return ParseResult(
            text="body text",
            metadata={"width": 8, "height": 8, "source_path": "SHOULD_NOT_WIN"},
        )


def test_deriver_metadata_reaches_document(tmp_path):
    (tmp_path / "a.weird").write_text("x", encoding="utf-8")
    src = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.weird")),
        parsers={"weird": _MetaDeriver()},
    )
    doc = list(src.iter_documents())[0]
    assert doc.content == "body text"
    assert doc.metadata["width"] == 8
    assert doc.metadata["height"] == 8


def test_source_keys_win_over_deriver_keys(tmp_path):
    """source_path is chunkshop's provenance, not the deriver's to overwrite."""
    (tmp_path / "a.weird").write_text("x", encoding="utf-8")
    src = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.weird")),
        parsers={"weird": _MetaDeriver()},
    )
    doc = list(src.iter_documents())[0]
    assert doc.metadata["source_path"] == str(tmp_path / "a.weird")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_files_deriver_metadata.py -q`
Expected: FAIL — `KeyError: 'width'` (deriver metadata is currently discarded).

- [ ] **Step 3: Write minimal implementation**

In `python/src/chunkshop/sources/files.py`, replace the body of `_document_for` (currently lines 122-136):

```python
    def _document_for(self, path: Path, content_hash: Optional[str] = None) -> Document:
        ext = path.suffix.lower().lstrip(".")
        parser = get_parser(ext, self._parsers)
        result = call_deriver(parser, path)
        metadata = {
            **result.metadata,
            # chunkshop's own provenance always wins over a deriver's keys.
            "source_path": str(path),
            "parser": parser.__class__.__name__,
        }
        return Document(
            id=self._id_for(path),
            content=result.text,
            title=path.name,
            metadata=metadata,
            fingerprint=content_hash,
        )
```

Update the import line near the top of the file:

```python
from chunkshop.sources.parsers import DEFAULT_PARSERS, FileParser, call_deriver, get_parser
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_files_deriver_metadata.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Verify no regression in the existing files suite**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_files_dispatch.py tests/chunkshop/test_sources_files.py tests/chunkshop/test_files_incremental.py -q`
Expected: PASS, no modifications to those test files.

- [ ] **Step 6: Commit**

```bash
git add python/src/chunkshop/sources/files.py \
        python/tests/chunkshop/test_files_deriver_metadata.py
git commit -m "feat(files): merge deriver metadata into Document, source keys win"
```

---

### Task 3: Empty derivation becomes loud (fixes the shipping defect)

`sources/parsers/pdf.py` currently returns `""` for a scanned PDF via `(page.extract_text() or "")`. That document chunks into nothing and silently disappears from the index.

**Files:**
- Modify: `python/src/chunkshop/sources/parsers/pdf.py`
- Modify: `python/src/chunkshop/config.py` (add `FilesSource.on_empty`)
- Modify: `python/src/chunkshop/sources/files.py` (honour `on_empty`)
- Test: `python/tests/chunkshop/test_empty_derivation.py`

**Interfaces:**
- Consumes: `EmptyDerivationError` from Task 1.
- Produces: `FilesSource.on_empty: Literal["error", "skip", "index"] = "error"`. `iter_documents()` raises on empty under `"error"`, omits the document under `"skip"`, and preserves pre-1.0 behaviour under `"index"`.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_empty_derivation.py`:

```python
from pathlib import Path

import pytest

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers.base import EmptyDerivationError


class _EmptyDeriver:
    supported_extensions = ["blank"]

    def derive(self, path: Path) -> str:
        return "   \n  "  # whitespace only — as good as empty


def _src(tmp_path, **kw):
    (tmp_path / "a.blank").write_text("x", encoding="utf-8")
    return FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.blank"), **kw),
        parsers={"blank": _EmptyDeriver()},
    )


def test_empty_derivation_raises_by_default(tmp_path):
    with pytest.raises(EmptyDerivationError, match="a.blank"):
        list(_src(tmp_path).iter_documents())


def test_on_empty_skip_omits_the_document(tmp_path):
    assert list(_src(tmp_path, on_empty="skip").iter_documents()) == []


def test_on_empty_index_preserves_legacy_behaviour(tmp_path):
    docs = list(_src(tmp_path, on_empty="index").iter_documents())
    assert len(docs) == 1
    assert docs[0].content.strip() == ""


def test_pdf_with_no_extractable_text_raises(tmp_path):
    """The actual shipping defect: a scanned PDF silently indexed as empty."""
    pytest.importorskip("pypdf")
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    target = tmp_path / "scanned.pdf"
    with target.open("wb") as fh:
        writer.write(fh)

    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.pdf")))
    with pytest.raises(EmptyDerivationError, match="scanned.pdf"):
        list(src.iter_documents())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_empty_derivation.py -q`
Expected: FAIL — `pydantic_core.ValidationError` for the unknown `on_empty` field (config is `extra="forbid"`), and the PDF case fails because no exception is raised.

- [ ] **Step 3: Add the config field**

In `python/src/chunkshop/config.py`, extend `class FilesSource(_Base)`:

```python
class FilesSource(_Base):
    type: Literal["files"]
    glob: str
    id_from: Literal["path", "stem", "sha1"] = "stem"
    encoding: str = "utf-8"
    incremental: Optional[FilesIncrementalSettings] = None
    on_empty: Literal["error", "skip", "index"] = "error"
    """What to do when a deriver produces no text.

    ``error`` (default) raises EmptyDerivationError — a scanned PDF that
    yields nothing is a failure, not an empty success. ``skip`` omits the
    document and lets the run continue. ``index`` restores the pre-1.0
    behaviour of indexing an empty document; it is retained only for
    backward compatibility and is not recommended.
    """
```

- [ ] **Step 4: Honour it in `FilesSource`**

In `python/src/chunkshop/sources/files.py`, change `iter_documents` to skip `None`, and have `_document_for` return `Optional[Document]`:

```python
    def iter_documents(self) -> Iterator[Document]:
        paths = self.current_paths()
        if not paths:
            raise ValueError(f"no files matched glob: {self.cfg.glob}")
        for p in paths:
            doc = self._document_for(Path(p))
            if doc is not None:
                yield doc
```

And inside `_document_for`, immediately after `result = call_deriver(parser, path)`:

```python
        if not result.text.strip():
            mode = self.cfg.on_empty
            if mode == "error":
                raise EmptyDerivationError(
                    f"{path} produced no text via {parser.__class__.__name__}. "
                    f"A scanned PDF or image needs an OCR rung; set "
                    f"source.on_empty='skip' to continue past these."
                )
            if mode == "skip":
                return None
```

Import `EmptyDerivationError` from `chunkshop.sources.parsers`, and change the signature to `-> Optional[Document]`.

- [ ] **Step 4b: Fix the incremental path too**

`_document_for` now returns `Optional[Document]`, and `iter_changes_since` also calls it
(`sources/files.py:94`). Without this it yields `None` straight into the runner. Change that line:

```python
            doc = self._document_for(path, content_hash)
            if doc is not None:
                yield doc
```

Add this test to `python/tests/chunkshop/test_empty_derivation.py`:

```python
def test_on_empty_skip_also_applies_to_the_incremental_path(tmp_path):
    """iter_changes_since must not yield None for a skipped document."""
    (tmp_path / "a.blank").write_text("x", encoding="utf-8")
    src = FilesSource(
        Cfg(
            type="files",
            glob=str(tmp_path / "*.blank"),
            on_empty="skip",
            incremental={"cursor_path": str(tmp_path / "cursor.json")},
        ),
        parsers={"blank": _EmptyDeriver()},
    )
    docs = list(src.iter_changes_since(src.empty_cursor()))
    assert docs == [], "no None may leak into the runner"
```

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_empty_derivation.py -q`
Expected: PASS

- [ ] **Step 5: Stop the PDF parser swallowing empties**

In `python/src/chunkshop/sources/parsers/pdf.py`, the extraction line becomes explicit rather than defensive:

```python
            reader = pypdf.PdfReader(str(path))
            pages = [(page.extract_text() or "") for page in reader.pages]
            return "\n".join(pages)
```

(Behaviour is unchanged here — the parser still returns a possibly-empty string; `FilesSource` is what decides. Keeping the decision in one place means every deriver gets the same treatment, not just PDF.)

- [ ] **Step 6: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_empty_derivation.py -q`
Expected: PASS (4 passed)

- [ ] **Step 7: Run the full suite for regressions**

Run: `cd python && uv run --no-sync pytest -q`
Expected: PASS. If any existing test relied on an empty document being indexed, change **that test** to pass `on_empty="index"` explicitly and note it in the commit body — do not weaken the default.

- [ ] **Step 8: Commit**

```bash
git add python/src/chunkshop/sources/parsers/pdf.py \
        python/src/chunkshop/config.py \
        python/src/chunkshop/sources/files.py \
        python/tests/chunkshop/test_empty_derivation.py
git commit -m "fix(files): empty derivation raises instead of silently indexing an empty doc"
```

---

### Task 4: Derivation provenance — the hash and the stamp

**Files:**
- Create: `python/src/chunkshop/sources/derivers/__init__.py` (empty package marker for now)
- Create: `python/src/chunkshop/sources/derivers/provenance.py`
- Test: `python/tests/chunkshop/test_derivation_provenance.py`

**Interfaces:**
- Consumes: `ParseResult` from Task 1.
- Produces: `derivation_hash(rung: str, model_id: str, model_version: str, params: dict) -> str`; `stamp_provenance(result: ParseResult, *, rung, model_id, model_version, params) -> ParseResult` which returns a new `ParseResult` whose metadata contains `derived.rung`, `derived.model`, `derived.model_version`, `derived.ts`, `derived.params_hash`, and top-level `content_origin="derived"`.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_derivation_provenance.py`:

```python
from chunkshop.sources.derivers.provenance import derivation_hash, stamp_provenance
from chunkshop.sources.parsers.result import ParseResult


def test_hash_is_stable_for_same_inputs():
    a = derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 3})
    b = derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 3})
    assert a == b


def test_hash_ignores_dict_ordering():
    a = derivation_hash("r", "m", "1", {"x": 1, "y": 2})
    b = derivation_hash("r", "m", "1", {"y": 2, "x": 1})
    assert a == b


def test_hash_changes_when_any_input_changes():
    base = derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 3})
    assert derivation_hash("ocr", "clip-ViT-B-32", "1", {"top_k": 3}) != base
    assert derivation_hash("clip_tags", "other", "1", {"top_k": 3}) != base
    assert derivation_hash("clip_tags", "clip-ViT-B-32", "2", {"top_k": 3}) != base
    assert derivation_hash("clip_tags", "clip-ViT-B-32", "1", {"top_k": 5}) != base


def test_stamp_adds_provenance_and_origin():
    out = stamp_provenance(
        ParseResult(text="a cat", metadata={"width": 8}),
        rung="clip_tags",
        model_id="Qdrant/clip-ViT-B-32-text",
        model_version="1",
        params={"top_k": 3},
    )
    assert out.text == "a cat"
    assert out.metadata["width"] == 8                      # preserved
    assert out.metadata["content_origin"] == "derived"
    assert out.metadata["derived_rung"] == "clip_tags"
    assert out.metadata["derived"]["model"] == "Qdrant/clip-ViT-B-32-text"
    assert out.metadata["derived"]["model_version"] == "1"
    assert out.metadata["derived"]["params_hash"]
    assert out.metadata["derived"]["ts"]


def test_stamp_does_not_mutate_the_input():
    original = ParseResult(text="x", metadata={"a": 1})
    stamp_provenance(original, rung="r", model_id="m", model_version="1", params={})
    assert original.metadata == {"a": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_derivation_provenance.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunkshop.sources.derivers'`

- [ ] **Step 3: Write minimal implementation**

Create `python/src/chunkshop/sources/derivers/__init__.py`:

```python
"""Media derivers: turn non-text bytes into text plus metadata.

Every deriver's output is stamped with provenance and memoised on
(content_hash, derivation_hash) so nothing is derived twice and nothing is
written without attribution.
"""
```

Create `python/src/chunkshop/sources/derivers/provenance.py`:

```python
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from chunkshop.sources.parsers.result import ParseResult

# Bump when the *shape* of the derivation hash changes, so old cache entries
# invalidate cleanly instead of being served against a new formula.
CACHE_SCHEMA_VERSION = "1"


def _params_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def derivation_hash(
    rung: str, model_id: str, model_version: str, params: dict
) -> str:
    """Identity of *how* a derivation was produced.

    Combined with the content hash this forms the cache key, which means the
    cache key IS the provenance record: change the model, the version, or any
    parameter and the derivation is redone rather than silently reused.
    """
    parts = [CACHE_SCHEMA_VERSION, rung, model_id, model_version, _params_hash(params)]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def stamp_provenance(
    result: ParseResult,
    *,
    rung: str,
    model_id: str,
    model_version: str,
    params: dict,
) -> ParseResult:
    """Return a new ParseResult carrying full derivation provenance."""
    metadata = {
        **result.metadata,
        "content_origin": "derived",
        "derived_rung": rung,
        "derived": {
            "rung": rung,
            "model": model_id,
            "model_version": model_version,
            "params_hash": _params_hash(params),
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    }
    return ParseResult(text=result.text, metadata=metadata)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_derivation_provenance.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sources/derivers/ \
        python/tests/chunkshop/test_derivation_provenance.py
git commit -m "feat(derivers): derivation hash + provenance stamping"
```

---

### Task 5: The derivation cache

The expensive-work memo. Lives inside the deriver wrapper, **not** in `FilesSource`.

**Files:**
- Create: `python/src/chunkshop/sources/derivers/cache.py`
- Test: `python/tests/chunkshop/test_derivation_cache.py`

**Interfaces:**
- Consumes: `derivation_hash` (Task 4), `ParseResult` (Task 1).
- Produces: `DerivationCache(root: Path)` with `get(content_hash, derivation_hash) -> ParseResult | None`, `put(content_hash, derivation_hash, result) -> None`, `purge() -> int`; and `cached_derive(cache, content_hash, derivation_hash, compute) -> tuple[ParseResult, bool]` returning `(result, was_cache_hit)`.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_derivation_cache.py`:

```python
from chunkshop.sources.derivers.cache import DerivationCache, cached_derive
from chunkshop.sources.parsers.result import ParseResult


def test_miss_then_hit(tmp_path):
    cache = DerivationCache(root=tmp_path)
    assert cache.get("c1", "d1") is None
    cache.put("c1", "d1", ParseResult(text="cat", metadata={"k": 1}))
    got = cache.get("c1", "d1")
    assert got is not None
    assert got.text == "cat"
    assert got.metadata == {"k": 1}


def test_different_derivation_hash_is_a_miss(tmp_path):
    """Changing the prompt/model/params must re-derive, never serve stale."""
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="old"))
    assert cache.get("c1", "d2") is None


def test_different_content_hash_is_a_miss(tmp_path):
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="old"))
    assert cache.get("c2", "d1") is None


def test_cached_derive_computes_once(tmp_path):
    cache = DerivationCache(root=tmp_path)
    calls = []

    def compute():
        calls.append(1)
        return ParseResult(text="expensive")

    r1, hit1 = cached_derive(cache, "c1", "d1", compute)
    r2, hit2 = cached_derive(cache, "c1", "d1", compute)

    assert r1.text == r2.text == "expensive"
    assert hit1 is False and hit2 is True
    assert len(calls) == 1, "second call must not recompute"


def test_purge_empties_the_cache(tmp_path):
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="x"))
    cache.put("c2", "d1", ParseResult(text="y"))
    assert cache.purge() == 2
    assert cache.get("c1", "d1") is None


def test_corrupt_entry_is_treated_as_a_miss(tmp_path):
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="x"))
    for f in tmp_path.rglob("*.json"):
        f.write_text("{not json", encoding="utf-8")
    assert cache.get("c1", "d1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_derivation_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunkshop.sources.derivers.cache'`

- [ ] **Step 3: Write minimal implementation**

Create `python/src/chunkshop/sources/derivers/cache.py`:

```python
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from chunkshop.sources.parsers.result import ParseResult


def default_cache_root() -> Path:
    env = os.environ.get("CHUNKSHOP_CACHE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "chunkshop" / "derived"


class DerivationCache:
    """Memoises derivations on (content_hash, derivation_hash).

    The key is the provenance: a different model, prompt, or parameter is a
    different derivation_hash and therefore a miss. Re-chunking or changing
    the target table does not touch either half of the key, so it re-derives
    nothing.

    Entries hold derived text — for some corpora that is sensitive material.
    The root is configurable so it can live on an encrypted volume, and
    ``purge()`` deletes everything. There is no encryption-at-rest in v1.
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else default_cache_root()

    def _path(self, content_hash: str, derivation_hash: str) -> Path:
        key = hashlib.sha256(f"{content_hash}\x00{derivation_hash}".encode()).hexdigest()
        return self.root / key[:2] / f"{key}.json"

    def get(self, content_hash: str, derivation_hash: str) -> Optional[ParseResult]:
        path = self._path(content_hash, derivation_hash)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return ParseResult(text=payload["text"], metadata=payload["metadata"])
        except (OSError, ValueError, KeyError):
            # Missing or corrupt entries are misses, never errors.
            return None

    def put(self, content_hash: str, derivation_hash: str, result: ParseResult) -> None:
        path = self._path(content_hash, derivation_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"text": result.text, "metadata": result.metadata})
        # Atomic write so a crash mid-write cannot leave a half entry.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def purge(self) -> int:
        count = 0
        for f in self.root.rglob("*.json"):
            f.unlink(missing_ok=True)
            count += 1
        return count


def cached_derive(
    cache: Optional[DerivationCache],
    content_hash: str,
    derivation_hash: str,
    compute: Callable[[], ParseResult],
) -> tuple[ParseResult, bool]:
    """Return (result, was_cache_hit). A None cache disables memoisation."""
    if cache is None:
        return compute(), False
    hit = cache.get(content_hash, derivation_hash)
    if hit is not None:
        return hit, True
    result = compute()
    cache.put(content_hash, derivation_hash, result)
    return result, False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_derivation_cache.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sources/derivers/cache.py \
        python/tests/chunkshop/test_derivation_cache.py
git commit -m "feat(derivers): provenance-keyed derivation cache with atomic writes"
```

---

### Task 6: Rung 0 — Pillow image metadata deriver (keyless, free)

**Files:**
- Create: `python/src/chunkshop/sources/derivers/image_meta.py`
- Test: `python/tests/chunkshop/test_deriver_image_meta.py`

**Interfaces:**
- Consumes: `ParseResult` (Task 1), `stamp_provenance` (Task 4).
- Produces: `ImageMetaDeriver(path_segments: bool = True)` with `supported_extensions = ["png","jpg","jpeg","webp","gif","bmp","tiff","tif"]` and `derive(path) -> ParseResult`. Emits metadata keys `width`, `height`, `image_format`, `image_mode`, `aspect_ratio`, `has_alpha`, and text built from the filename plus path segments.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_deriver_image_meta.py`:

```python
import pytest

from chunkshop.sources.derivers.image_meta import ImageMetaDeriver


@pytest.fixture
def png(tmp_path):
    Image = pytest.importorskip("PIL.Image", reason="Pillow comes with fastembed")
    p = tmp_path / "sprites" / "goblin_warrior.png"
    p.parent.mkdir(parents=True)
    Image.new("RGBA", (64, 32), (255, 0, 0, 255)).save(p)
    return p


def test_emits_dimensions_and_format(png):
    result = ImageMetaDeriver().derive(png)
    assert result.metadata["width"] == 64
    assert result.metadata["height"] == 32
    assert result.metadata["image_format"] == "PNG"
    assert result.metadata["has_alpha"] is True
    assert result.metadata["aspect_ratio"] == pytest.approx(2.0)


def test_text_includes_humanised_filename(png):
    text = ImageMetaDeriver().derive(png).text
    assert "goblin" in text.lower()
    assert "warrior" in text.lower()


def test_text_includes_path_segments(png):
    text = ImageMetaDeriver().derive(png).text
    assert "sprites" in text.lower()


def test_provenance_is_stamped(png):
    md = ImageMetaDeriver().derive(png).metadata
    assert md["content_origin"] == "derived"
    assert md["derived_rung"] == "image_meta"
    assert md["derived"]["model"] == "deterministic"


def test_never_returns_empty_text(png):
    """Rung 0 is hallucination-proof AND always produces something."""
    assert ImageMetaDeriver().derive(png).text.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_deriver_image_meta.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunkshop.sources.derivers.image_meta'`

- [ ] **Step 3: Write minimal implementation**

Create `python/src/chunkshop/sources/derivers/image_meta.py`:

```python
"""Rung 0 for images: deterministic metadata, no model, no network.

Pillow arrives transitively with fastembed (a core dependency), so this rung
needs no extra and cannot hallucinate. It is always on.
"""

from __future__ import annotations

import re
from pathlib import Path

from chunkshop.sources.derivers.provenance import stamp_provenance
from chunkshop.sources.parsers.result import ParseResult

_SPLIT = re.compile(r"[_\-.]+")


def humanise(stem: str) -> str:
    """`goblin_warrior-02` -> `goblin warrior 02`."""
    return " ".join(part for part in _SPLIT.split(stem) if part)


class ImageMetaDeriver:
    supported_extensions = [
        "png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif",
    ]

    def __init__(self, path_segments: bool = True, segment_depth: int = 3):
        self.path_segments = path_segments
        self.segment_depth = segment_depth

    def derive(self, path: Path) -> ParseResult:
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - Pillow ships with fastembed
            raise RuntimeError(
                "image metadata requires Pillow, normally installed with fastembed."
            ) from exc

        with Image.open(path) as img:
            width, height = img.size
            fmt = img.format or path.suffix.lstrip(".").upper()
            mode = img.mode
            has_alpha = mode in ("RGBA", "LA", "PA") or "transparency" in img.info

        words = [humanise(path.stem)]
        if self.path_segments:
            segments = [p for p in path.parent.parts[-self.segment_depth:] if p not in ("/", "")]
            words.extend(humanise(s) for s in segments)

        result = ParseResult(
            text=" ".join(w for w in words if w),
            metadata={
                "width": width,
                "height": height,
                "image_format": fmt,
                "image_mode": mode,
                "aspect_ratio": round(width / height, 4) if height else 0.0,
                "has_alpha": bool(has_alpha),
            },
        )
        return stamp_provenance(
            result,
            rung="image_meta",
            model_id="deterministic",
            model_version="1",
            params={"path_segments": self.path_segments,
                    "segment_depth": self.segment_depth},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_deriver_image_meta.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sources/derivers/image_meta.py \
        python/tests/chunkshop/test_deriver_image_meta.py
git commit -m "feat(derivers): rung 0 image metadata deriver (Pillow, keyless)"
```

---

### Task 7: Rung 1 — CLIP zero-shot tagging deriver (keyless, ~18ms/image)

Embeds a configurable label vocabulary with the CLIP **text** tower, then tags each image by nearest labels. Output is ordinary text landing in the ordinary text table.

**Files:**
- Create: `python/src/chunkshop/sources/derivers/clip_tags.py`
- Test: `python/tests/chunkshop/test_deriver_clip_tags.py`

**Interfaces:**
- Consumes: `ImageMetaDeriver` (Task 6, for the metadata half), `stamp_provenance` (Task 4).
- Produces: `ClipTagsDeriver(vocabulary: list[str], top_k: int = 3, min_score: float = 0.20, vision_model: str = "Qdrant/clip-ViT-B-32-vision", text_model: str = "Qdrant/clip-ViT-B-32-text")` with `derive(path) -> ParseResult`. Emits `clip_tags: list[str]`, `clip_scores: list[float]`, plus everything `ImageMetaDeriver` emits.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_deriver_clip_tags.py`:

```python
import pytest

from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

VOCAB = [
    "a solid red square",
    "a solid blue square",
    "a photograph of a cat",
]


@pytest.fixture
def images(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (224, 224), (220, 30, 30)).save(red)
    Image.new("RGB", (224, 224), (30, 30, 220)).save(blue)
    return red, blue


def test_requires_a_non_empty_vocabulary():
    with pytest.raises(ValueError, match="vocabulary"):
        ClipTagsDeriver(vocabulary=[])


@pytest.mark.slow
def test_tags_are_text_and_correct(images):
    """Downloads ~0.6GB of ONNX weights on first run; marked slow."""
    pytest.importorskip("fastembed")
    red, blue = images
    d = ClipTagsDeriver(vocabulary=VOCAB, top_k=1)

    red_result = d.derive(red)
    assert red_result.metadata["clip_tags"][0] == "a solid red square"
    assert "red" in red_result.text.lower()

    blue_result = d.derive(blue)
    assert blue_result.metadata["clip_tags"][0] == "a solid blue square"


@pytest.mark.slow
def test_provenance_names_the_models(images):
    pytest.importorskip("fastembed")
    md = ClipTagsDeriver(vocabulary=VOCAB).derive(images[0]).metadata
    assert md["content_origin"] == "derived"
    assert md["derived_rung"] == "clip_tags"
    assert "clip-ViT-B-32" in md["derived"]["model"]


@pytest.mark.slow
def test_includes_rung_0_metadata(images):
    """clip_tags is additive over image_meta, not a replacement."""
    pytest.importorskip("fastembed")
    md = ClipTagsDeriver(vocabulary=VOCAB).derive(images[0]).metadata
    assert md["width"] == 224
    assert md["image_format"] == "PNG"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_deriver_clip_tags.py -q -m "not slow"`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunkshop.sources.derivers.clip_tags'`

- [ ] **Step 3: Write minimal implementation**

Create `python/src/chunkshop/sources/derivers/clip_tags.py`:

```python
"""Rung 1 for images: zero-shot tagging with CLIP, keyless and offline.

Embeds a label vocabulary with the CLIP text tower and tags each image by
nearest label. Costs no new dependency — fastembed is already core — and no
API key. Output is TEXT, so it lands in the ordinary text plane alongside
email and PDF and is searched by the hybrid search that already ships.

The vocabulary is the whole game: a generic vocabulary produces generic
tags. It is user-configurable and bakeoff-tunable by design; chunkshop
asserts no default winner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from chunkshop.sources.derivers.image_meta import ImageMetaDeriver
from chunkshop.sources.derivers.provenance import stamp_provenance
from chunkshop.sources.parsers.result import ParseResult

_VISION_DEFAULT = "Qdrant/clip-ViT-B-32-vision"
_TEXT_DEFAULT = "Qdrant/clip-ViT-B-32-text"


class ClipTagsDeriver:
    supported_extensions = ImageMetaDeriver.supported_extensions

    def __init__(
        self,
        vocabulary: Sequence[str],
        top_k: int = 3,
        min_score: float = 0.20,
        vision_model: str = _VISION_DEFAULT,
        text_model: str = _TEXT_DEFAULT,
    ):
        if not vocabulary:
            raise ValueError(
                "ClipTagsDeriver needs a non-empty vocabulary; a generic "
                "vocabulary produces generic tags, so choose it for your corpus."
            )
        self.vocabulary = list(vocabulary)
        self.top_k = top_k
        self.min_score = min_score
        self.vision_model = vision_model
        self.text_model = text_model
        self._meta = ImageMetaDeriver()
        self._vision = None
        self._label_vectors = None

    def _ensure_models(self):
        """Lazy: nothing downloads at import or install (NN-6)."""
        if self._vision is not None:
            return
        import numpy as np
        from fastembed import ImageEmbedding, TextEmbedding

        self._vision = ImageEmbedding(self.vision_model)
        text = TextEmbedding(self.text_model)
        vectors = [
            v / np.linalg.norm(v) for v in text.embed(self.vocabulary)
        ]
        self._label_vectors = np.vstack(vectors)

    def derive(self, path: Path) -> ParseResult:
        import numpy as np

        self._ensure_models()
        base = self._meta.derive(path)

        vec = next(iter(self._vision.embed([str(path)])))
        vec = vec / np.linalg.norm(vec)
        scores = self._label_vectors @ vec

        order = np.argsort(scores)[::-1][: self.top_k]
        tags, tag_scores = [], []
        for i in order:
            score = float(scores[i])
            if score >= self.min_score:
                tags.append(self.vocabulary[i])
                tag_scores.append(round(score, 4))

        text = " ".join([base.text, *tags]).strip()
        result = ParseResult(
            text=text,
            metadata={**base.metadata, "clip_tags": tags, "clip_scores": tag_scores},
        )
        return stamp_provenance(
            result,
            rung="clip_tags",
            model_id=f"{self.vision_model}+{self.text_model}",
            model_version="1",
            params={
                "vocabulary": self.vocabulary,
                "top_k": self.top_k,
                "min_score": self.min_score,
            },
        )
```

- [ ] **Step 4: Run the fast test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_deriver_clip_tags.py -q -m "not slow"`
Expected: PASS (1 passed, 3 deselected)

- [ ] **Step 5: Run the slow tests once, manually, to prove the real behaviour**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_deriver_clip_tags.py -q -m slow`
Expected: PASS (3 passed). First run downloads ~0.6 GB of ONNX weights; subsequent runs are fast. If `red.png` does not rank `"a solid red square"` first, **stop and investigate** — do not weaken the assertion.

- [ ] **Step 6: Commit**

```bash
git add python/src/chunkshop/sources/derivers/clip_tags.py \
        python/tests/chunkshop/test_deriver_clip_tags.py
git commit -m "feat(derivers): rung 1 CLIP zero-shot image tagging (keyless, no extra)"
```

---

### Task 8: Wire derivers into config and `FilesSource`

**Files:**
- Modify: `python/src/chunkshop/config.py` (deriver config union + `FilesSource.derive`)
- Modify: `python/src/chunkshop/sources/derivers/__init__.py` (`load_deriver`)
- Modify: `python/src/chunkshop/sources/files.py` (build the deriver table, apply the cache)
- Test: `python/tests/chunkshop/test_derive_config.py`

**Interfaces:**
- Consumes: `ImageMetaDeriver`, `ClipTagsDeriver`, `DerivationCache`, `cached_derive`, `derivation_hash`.
- Produces: `load_deriver(cfg) -> Deriver`; `FilesSource.derive: Optional[dict[str, DeriverConfig]]` keyed by pipe-separated extension patterns (`"png|jpg|webp"`); `FilesSource.cache: Optional[DerivationCacheSettings]`.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_derive_config.py`:

```python
import pytest

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.derivers import load_deriver
from chunkshop.sources.derivers.image_meta import ImageMetaDeriver


def test_image_meta_rung_loads():
    d = load_deriver({"rung": "image_meta"})
    assert isinstance(d, ImageMetaDeriver)


def test_clip_tags_rung_loads_with_vocabulary():
    from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

    d = load_deriver({"rung": "clip_tags", "vocabulary": ["a cat", "a dog"]})
    assert isinstance(d, ClipTagsDeriver)
    assert d.vocabulary == ["a cat", "a dog"]


def test_unknown_rung_is_rejected():
    with pytest.raises(ValueError, match="unknown rung"):
        load_deriver({"rung": "telepathy"})


def test_derive_config_accepts_pipe_separated_extensions():
    cfg = Cfg(
        type="files",
        glob="/tmp/**/*",
        derive={"png|jpg|webp": {"rung": "image_meta"}},
    )
    assert "png|jpg|webp" in cfg.derive


def test_typo_in_derive_block_is_rejected():
    """extra='forbid' must catch config typos, not ignore them."""
    with pytest.raises(Exception):
        Cfg(
            type="files",
            glob="/tmp/**/*",
            derive={"png": {"rung": "clip_tags", "vocabluary": ["x"]}},
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_derive_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_deriver'`

- [ ] **Step 3: Add config models**

In `python/src/chunkshop/config.py`, above `class FilesSource`:

```python
class ImageMetaDeriverConfig(_Base):
    rung: Literal["image_meta"]
    path_segments: bool = True
    segment_depth: int = 3


class ClipTagsDeriverConfig(_Base):
    rung: Literal["clip_tags"]
    vocabulary: list[str]
    top_k: int = 3
    min_score: float = 0.20
    vision_model: str = "Qdrant/clip-ViT-B-32-vision"
    text_model: str = "Qdrant/clip-ViT-B-32-text"


DeriverConfig = Annotated[
    Union[ImageMetaDeriverConfig, ClipTagsDeriverConfig],
    Field(discriminator="rung"),
]


class DerivationCacheSettings(_Base):
    """Memoises derived text on (content_hash, derivation_hash).

    Entries contain derived text, which for some corpora is sensitive: point
    ``root`` at an encrypted volume, or set ``enabled: false`` to keep derived
    text only in the target database. No encryption-at-rest in v1.
    """
    enabled: bool = True
    root: Optional[str] = None
```

Then extend `FilesSource`:

```python
    derive: Optional[dict[str, DeriverConfig]] = None
    cache: DerivationCacheSettings = DerivationCacheSettings()
```

- [ ] **Step 4: Add the loader**

Append to `python/src/chunkshop/sources/derivers/__init__.py`:

```python
from __future__ import annotations

from typing import Any


def load_deriver(cfg: Any):
    """Build a deriver from a config object or an equivalent dict."""
    data = cfg if isinstance(cfg, dict) else cfg.model_dump()
    rung = data.get("rung")

    if rung == "image_meta":
        from chunkshop.sources.derivers.image_meta import ImageMetaDeriver

        return ImageMetaDeriver(
            path_segments=data.get("path_segments", True),
            segment_depth=data.get("segment_depth", 3),
        )
    if rung == "clip_tags":
        from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

        return ClipTagsDeriver(
            vocabulary=data["vocabulary"],
            top_k=data.get("top_k", 3),
            min_score=data.get("min_score", 0.20),
            vision_model=data.get("vision_model", "Qdrant/clip-ViT-B-32-vision"),
            text_model=data.get("text_model", "Qdrant/clip-ViT-B-32-text"),
        )
    raise ValueError(f"unknown rung: {rung!r}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_derive_config.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Wire into `FilesSource.__init__`**

In `python/src/chunkshop/sources/files.py`, at the end of `__init__`:

```python
        # Derive config layers over the parser table: one deriver per
        # extension listed in a pipe-separated key ("png|jpg|webp").
        if cfg.derive:
            from chunkshop.sources.derivers import load_deriver

            for pattern, deriver_cfg in cfg.derive.items():
                deriver = load_deriver(deriver_cfg)
                for ext in pattern.split("|"):
                    self._parsers[ext.strip().lower().lstrip(".")] = deriver

        self._cache = None
        if cfg.cache.enabled:
            from chunkshop.sources.derivers.cache import DerivationCache

            self._cache = DerivationCache(
                root=Path(cfg.cache.root) if cfg.cache.root else None
            )
```

- [ ] **Step 7: Apply the cache in `_document_for`**

Replace the `result = call_deriver(parser, path)` line with:

```python
        if content_hash is None:
            content_hash = _hash_bytes(path.read_bytes())
        d_hash = getattr(parser, "derivation_hash", None)
        if callable(d_hash):
            d_hash = d_hash()
        if d_hash is None:
            d_hash = parser.__class__.__name__
        result, _was_hit = cached_derive(
            self._cache, content_hash, d_hash, lambda: call_deriver(parser, path)
        )
```

Import `cached_derive` from `chunkshop.sources.derivers.cache`.

Then add a `derivation_hash()` method to **both** `ImageMetaDeriver` and `ClipTagsDeriver`, returning the same value `stamp_provenance` uses — e.g. for `ClipTagsDeriver`:

```python
    def derivation_hash(self) -> str:
        from chunkshop.sources.derivers.provenance import derivation_hash

        return derivation_hash(
            "clip_tags",
            f"{self.vision_model}+{self.text_model}",
            "1",
            {"vocabulary": self.vocabulary, "top_k": self.top_k,
             "min_score": self.min_score},
        )
```

and for `ImageMetaDeriver`:

```python
    def derivation_hash(self) -> str:
        from chunkshop.sources.derivers.provenance import derivation_hash

        return derivation_hash(
            "image_meta", "deterministic", "1",
            {"path_segments": self.path_segments, "segment_depth": self.segment_depth},
        )
```

- [ ] **Step 8: Write the cache-integration test**

Create `python/tests/chunkshop/test_files_derive_cache.py`:

```python
from pathlib import Path

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers.result import ParseResult

CALLS = []


class _Counting:
    supported_extensions = ["weird"]

    def derivation_hash(self) -> str:
        return "fixed-hash-v1"

    def derive(self, path: Path) -> ParseResult:
        CALLS.append(str(path))
        return ParseResult(text="expensive", metadata={"k": 1})


def test_second_run_derives_nothing(tmp_path):
    CALLS.clear()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.weird").write_text("x", encoding="utf-8")
    cfg = Cfg(
        type="files",
        glob=str(corpus / "*.weird"),
        cache={"enabled": True, "root": str(tmp_path / "cache")},
    )

    list(FilesSource(cfg, parsers={"weird": _Counting()}).iter_documents())
    assert len(CALLS) == 1

    list(FilesSource(cfg, parsers={"weird": _Counting()}).iter_documents())
    assert len(CALLS) == 1, "cache hit must skip the expensive derivation"


def test_disabling_the_cache_always_derives(tmp_path):
    CALLS.clear()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.weird").write_text("x", encoding="utf-8")
    cfg = Cfg(type="files", glob=str(corpus / "*.weird"), cache={"enabled": False})

    for _ in range(2):
        list(FilesSource(cfg, parsers={"weird": _Counting()}).iter_documents())
    assert len(CALLS) == 2
```

- [ ] **Step 9: Run tests and the full suite**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_files_derive_cache.py -q && uv run --no-sync pytest -q`
Expected: PASS both. Existing text cells must be unaffected — the cache is keyed per deriver and text parsers are cheap either way.

- [ ] **Step 10: Commit**

```bash
git add python/src/chunkshop/config.py \
        python/src/chunkshop/sources/derivers/__init__.py \
        python/src/chunkshop/sources/derivers/image_meta.py \
        python/src/chunkshop/sources/derivers/clip_tags.py \
        python/src/chunkshop/sources/files.py \
        python/tests/chunkshop/test_derive_config.py \
        python/tests/chunkshop/test_files_derive_cache.py
git commit -m "feat(files): source.derive config + provenance-keyed derivation cache"
```

---

### Task 9: `content_origin` and `derived_rung` as promoted columns

Downstream tools must distinguish a verbatim email from a model's guess **in the query they already run** — not via an opt-in join.

**Files:**
- Modify: `python/src/chunkshop/config.py` (default promote columns)
- Test: `python/tests/chunkshop/test_content_origin_promoted.py`

**Interfaces:**
- Consumes: metadata keys `content_origin` and `derived_rung` from Task 4.
- Produces: both present as real columns on the chunks table whenever a derive block is configured.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_content_origin_promoted.py`:

```python
from chunkshop.config import PromoteColumn


def test_content_origin_column_name_is_safe():
    pc = PromoteColumn(path="content_origin", type="text")
    assert pc.column_name == "content_origin"


def test_derived_rung_column_name_is_safe():
    pc = PromoteColumn(path="derived_rung", type="text")
    assert pc.column_name == "derived_rung"


def test_default_media_promotions_are_exported():
    from chunkshop.config import DEFAULT_MEDIA_PROMOTIONS

    names = {pc.column_name for pc in DEFAULT_MEDIA_PROMOTIONS}
    assert {"content_origin", "derived_rung"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_content_origin_promoted.py -q`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_MEDIA_PROMOTIONS'`

- [ ] **Step 3: Write minimal implementation**

In `python/src/chunkshop/config.py`, after the `PromoteColumn` definition:

```python
DEFAULT_MEDIA_PROMOTIONS: list[PromoteColumn] = [
    PromoteColumn(path="content_origin", type="text"),
    PromoteColumn(path="derived_rung", type="text"),
]
"""Promoted by default whenever a cell configures ``source.derive``.

A VLM caption and a verbatim email are byte-identical once both are
``original_content``; these columns are what let a downstream consumer rank,
filter, or refuse derived content in the query it already runs.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_content_origin_promoted.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Merge the defaults when a derive block exists**

In `python/src/chunkshop/config.py`, add this validator to `class CellConfig`:

```python
    @model_validator(mode="after")
    def _promote_media_origin_columns(self) -> "CellConfig":
        """Media cells always get content_origin + derived_rung as real columns.

        Downstream consumers must be able to tell a verbatim document from a
        model's guess in the query they already run — not via an opt-in join.
        User-specified promotions win; defaults only fill gaps.
        """
        if not getattr(self.source, "derive", None):
            return self
        existing = {pc.column_name for pc in (self.target.promote_metadata or [])}
        additions = [
            pc for pc in DEFAULT_MEDIA_PROMOTIONS if pc.column_name not in existing
        ]
        if additions:
            self.target.promote_metadata = list(self.target.promote_metadata or []) + additions
        return self
```

Ensure `model_validator` is imported from `pydantic` at the top of the file.

Append to `python/tests/chunkshop/test_content_origin_promoted.py`:

```python
def test_media_cell_gets_origin_columns_automatically():
    import yaml

    from chunkshop.config import CellConfig

    cfg = CellConfig(**yaml.safe_load("""
    cell_name: t
    source:
      type: files
      glob: /tmp/*.png
      derive: {png: {rung: image_meta}}
    chunker: {type: fixed_overlap, window_words: 128, step_words: 128}
    embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    extractor: {type: none}
    target: {type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: t, mode: overwrite, hnsw: false}
    """))
    names = {pc.column_name for pc in cfg.target.promote_metadata}
    assert {"content_origin", "derived_rung"} <= names


def test_user_promotion_is_not_duplicated():
    import yaml

    from chunkshop.config import CellConfig

    cfg = CellConfig(**yaml.safe_load("""
    cell_name: t
    source:
      type: files
      glob: /tmp/*.png
      derive: {png: {rung: image_meta}}
    chunker: {type: fixed_overlap, window_words: 128, step_words: 128}
    embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    extractor: {type: none}
    target:
      type: sqlite
      dsn_env: SQLITE_PATH
      database: ignored
      table: t
      mode: overwrite
      hnsw: false
      promote_metadata: [{path: content_origin, type: text}]
    """))
    names = [pc.column_name for pc in cfg.target.promote_metadata]
    assert names.count("content_origin") == 1


def test_text_only_cell_gets_no_media_columns():
    """NN-6: a cell with no derive block is untouched."""
    import yaml

    from chunkshop.config import CellConfig

    cfg = CellConfig(**yaml.safe_load("""
    cell_name: t
    source: {type: files, glob: /tmp/*.md}
    chunker: {type: fixed_overlap, window_words: 128, step_words: 128}
    embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    extractor: {type: none}
    target: {type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: t, mode: overwrite, hnsw: false}
    """))
    assert not cfg.target.promote_metadata
```

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_content_origin_promoted.py -q`
Expected: PASS (6 passed)

- [ ] **Step 6: Run the full suite**

Run: `cd python && uv run --no-sync pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add python/src/chunkshop/config.py \
        python/tests/chunkshop/test_content_origin_promoted.py
git commit -m "feat(config): promote content_origin and derived_rung by default for media cells"
```

---

### Task 10: Extras, lazy loading, and the SC-13 base-install guarantee

**Files:**
- Modify: `python/pyproject.toml`
- Test: `python/tests/chunkshop/test_base_install_unaffected.py`

**Interfaces:**
- Consumes: everything above.
- Produces: extras `vision` (empty — CLIP is already core), `ocr`, `audio`, `media`.

- [ ] **Step 1: Write the failing test**

Create `python/tests/chunkshop/test_base_install_unaffected.py`:

```python
import importlib
import subprocess
import sys

import pytest


def test_importing_chunkshop_downloads_no_model():
    """NN-6: nothing may download at import time."""
    code = (
        "import sys; import chunkshop.sources.derivers.clip_tags as m; "
        "assert 'fastembed' not in sys.modules, 'fastembed imported at module import'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_deriver_module_imports_without_touching_models():
    m = importlib.import_module("chunkshop.sources.derivers.clip_tags")
    d = m.ClipTagsDeriver(vocabulary=["a cat"])
    assert d._vision is None, "models must be lazy until derive() is called"


def test_pyproject_declares_media_extras():
    import tomllib
    from pathlib import Path

    root = Path(__file__).parents[2] / "pyproject.toml"
    data = tomllib.loads(root.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert "ocr" in extras and "audio" in extras and "media" in extras
    assert extras["vision"] == [], "CLIP needs no extra — fastembed is already core"


def test_no_media_library_entered_core_dependencies():
    import tomllib
    from pathlib import Path

    root = Path(__file__).parents[2] / "pyproject.toml"
    data = tomllib.loads(root.read_text(encoding="utf-8"))
    core = " ".join(data["project"]["dependencies"]).lower()
    for banned in ("rapidocr", "faster-whisper", "torch", "transformers", "av>="):
        assert banned not in core, f"{banned} must live in an extra, not core"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_base_install_unaffected.py -q`
Expected: FAIL — `KeyError: 'ocr'`

- [ ] **Step 3: Write minimal implementation**

In `python/pyproject.toml`, under `[project.optional-dependencies]`, beside the existing extras:

```toml
# Media ingest. `vision` is deliberately EMPTY: CLIP image+text towers and
# Pillow arrive with fastembed, which is already a core dependency, so the
# keyless image path costs zero additional install weight.
vision = []
ocr = ["rapidocr>=3.0"]
audio = ["faster-whisper>=1.1", "av>=13", "soundfile>=0.12"]
media = ["chunkshop[vision,ocr,audio]"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_base_install_unaffected.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add python/pyproject.toml \
        python/tests/chunkshop/test_base_install_unaffected.py
git commit -m "feat(packaging): media extras; vision needs none (fastembed is core)"
```

---

### Task 11: The quickstart, and the CI clock that keeps it honest

SC-1's claim is only true if CI runs **the exact YAML printed in the README**.

**Files:**
- Create: `docs/samples/images-keyless.yaml`
- Create: `python/tests/chunkshop/test_quickstart_sc1.py`
- Modify: `README.md` (media quickstart section)

**Interfaces:**
- Consumes: everything above.
- Produces: a base-install-runnable YAML, and a test that parses the README's fenced YAML block and asserts it is byte-identical to the sample file.

- [ ] **Step 1: Write the sample YAML**

Create `docs/samples/images-keyless.yaml`:

```yaml
# Keyless image search. Base install only — no extras, no API key, no GPU.
cell_name: images_keyless
source:
  type: files
  glob: docs/samples/images/*.png
  id_from: stem
  derive:
    png|jpg|jpeg|webp:
      rung: clip_tags
      vocabulary:
        - a screenshot of a user interface
        - a photograph of a person
        - a chart or diagram
        - a game sprite or pixel art
        - a scanned document page
      top_k: 2
chunker:
  type: fixed_overlap
  window_words: 512
  step_words: 512
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
extractor:
  type: none
target:
  type: sqlite
  dsn_env: SQLITE_PATH
  database: ignored      # SQLite has no schema concept; required by config but unused
  table: images
  mode: overwrite
  hnsw: false            # sqlite-vec is brute-force KNN
```

- [ ] **Step 2: Write the failing test**

Create `python/tests/chunkshop/test_quickstart_sc1.py`:

```python
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parents[3]
SAMPLE = REPO / "docs" / "samples" / "images-keyless.yaml"
README = REPO / "README.md"


def test_sample_yaml_is_valid_config():
    from chunkshop.config import CellConfig

    cfg = CellConfig(**yaml.safe_load(SAMPLE.read_text(encoding="utf-8")))
    assert cfg.source.derive is not None


def test_sample_uses_no_extra_requiring_rung():
    """SC-1: the documented quickstart must run on a base install."""
    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    rungs = {block["rung"] for block in data["source"]["derive"].values()}
    needs_extra = {"ocr", "asr", "vlm_caption", "subprocess"}
    assert not (rungs & needs_extra), f"quickstart uses extras-requiring rungs: {rungs}"


def test_readme_block_matches_the_sample_file():
    """The claim rots the moment the README and the tested file diverge."""
    readme = README.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- BEGIN images-keyless -->\s*```yaml\n(.*?)```", readme, re.S
    )
    assert match, "README must contain a fenced images-keyless block"
    assert match.group(1).strip() == SAMPLE.read_text(encoding="utf-8").strip()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_quickstart_sc1.py -q`
Expected: FAIL — README has no `images-keyless` block.

- [ ] **Step 4: Add the README section**

In `README.md`, add a "Search a folder of images — no API key, no GPU" section containing the marker comments and the YAML copied verbatim from `docs/samples/images-keyless.yaml`, plus:

```bash
pip install chunkshop[sqlite]    # storage backend only — NO media extra, no key, no GPU
export SQLITE_PATH=/tmp/chunkshop-images.db
chunkshop ingest --config docs/samples/images-keyless.yaml
chunkshop search "a game sprite" --config docs/samples/images-keyless.yaml -k 1
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_quickstart_sc1.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Add the timed end-to-end CI job**

Create `.github/workflows/sc1-quickstart-clock.yml`:

```yaml
name: SC-1 quickstart clock

# The 5-minute keyless claim is the wedge. It rots silently unless CI runs
# the exact YAML printed in the README, on a base install, on a clock.
on:
  pull_request:
  push: {branches: [main]}

jobs:
  quickstart:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with: {python-version: "3.12"}

      - name: Install chunkshop with the storage extra ONLY (no media extras)
        run: pip install './python[sqlite]'

      - name: Assert no media extra leaked into the base install
        run: |
          python - <<'EOF'
          import importlib.util, sys
          for mod in ("rapidocr", "faster_whisper", "torch", "av"):
              if importlib.util.find_spec(mod) is not None:
                  sys.exit(f"FAIL: media package {mod} present without its extra")
          print("no media package present — the media path is genuinely keyless")
          EOF

      - name: Run the documented quickstart, on a clock
        env:
          SQLITE_PATH: /tmp/chunkshop-images.db
        run: |
          start=$(date +%s)
          chunkshop ingest --config docs/samples/images-keyless.yaml
          chunkshop search "a game sprite" \
            --config docs/samples/images-keyless.yaml -k 1 --json > result.json
          elapsed=$(( $(date +%s) - start ))
          echo "elapsed: ${elapsed}s (budget 300s, includes model download)"
          test "$elapsed" -lt 300 || { echo "FAIL: SC-1 budget exceeded"; exit 1; }

      - name: Assert the right image ranked first
        run: |
          python - <<'EOF'
          import json, sys
          hits = json.load(open("result.json"))
          top = (hits[0]["metadata"]["source_path"] if hits else "")
          if "sprite" not in top:
              sys.exit(f"FAIL: expected a sprite first, got {top!r}")
          print("top hit:", top)
          EOF
```

Commit a small fixture set at `docs/samples/images/` — at least `sprite_knight.png`,
`chart_revenue.png`, and `screenshot_settings.png`, each a few KB, generated with Pillow
so they are deterministic and reviewable. The search asserts `sprite_knight.png` ranks
first for the query `"a game sprite"`.

- [ ] **Step 7: Commit**

```bash
git add docs/samples/images-keyless.yaml README.md \
        python/tests/chunkshop/test_quickstart_sc1.py .github/workflows/
git commit -m "docs(quickstart): keyless image search + CI timing gate for SC-1"
```

---

## Success Criteria coverage for this plan

| SC | Task |
|---|---|
| SC-1 time-to-first-success | Task 11 (CI timing gate on the README's own YAML) |
| SC-2 zero model/endpoint choice | Task 11 (`test_sample_uses_no_extra_requiring_rung`) |
| SC-5 100% reconciliation | Task 3 (partial — the failure *reason* lands here; the run-level tally is Plan B) |
| SC-6 crash-resume | Task 5 + Task 8 (cache hits skip completed work) |
| SC-7 derivation cache | Task 5, Task 8 |
| SC-8 provenance | Task 4 |
| SC-9 no silent overwrite | Task 4 (a different model is a different key, so it cannot overwrite) |
| SC-10 content vs metadata | Task 2, Task 9 |
| SC-13 text-only users unaffected | Task 1 (source compat), Task 10 (extras + lazy load) |

**Deferred to Plan B (M4–M6):** SC-3, SC-4, SC-11, and the run-level reconciliation tally.
**Deferred to Plan D (M8):** SC-12.

## Notes for the executor

- Work in a worktree per repo convention: `git worktree add ../chunkshop-media-v1a -b feat/media-ingest-v1a`
- `uv run` re-syncs from `uv.lock` and can revert a manual install. Use `uv run --no-sync` throughout.
- `-m slow` tests download ~0.6 GB of ONNX weights on first run. Run them at least once locally before opening a PR; do not weaken their assertions to make them pass.
- If an existing test breaks because of Task 3's new default, fix **that test** by passing `on_empty="index"` explicitly and say so in the commit body. Do not change the default.
