# Files Incremental Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make chunkshop's local `files` source incremental — on re-run it reprocesses only new/changed files and purges chunks for deleted files, driven either as a library `IncrementalSource`/`PrunableSource` or, opt-in, by `chunkshop ingest` itself via a JSON cursor sidecar.

**Architecture:** `FilesSource` gains the `IncrementalSource` + `PrunableSource` protocol methods (mirroring `sources/s3.py`), keyed by a `{path: {h, mt, sz}}` cursor that carries a content hash, mtime, and size per file. Change detection defaults to content hash (survives `git checkout`); an opt-in `detect: mtime` fast-path skips unchanged files by stat alone. A new `chunkshop/incremental_cursor.py` persists the cursor atomically; `runner.run_cell` loads it, drives `iter_changes_since`, prunes deletions via `sink.delete_document`, and saves on success only (so a crash leaves the prior cursor intact and the next run safely re-upserts).

**Tech Stack:** Python 3.11+, pydantic v2 (`extra="forbid"` discriminated unions), stdlib `hashlib`/`os`/`json`, pytest (`tmp_path`), fastembed (`BAAI/bge-small-en-v1.5`, already used by the suite), the file-backed SQLite sink (no DB infrastructure required).

**Mission brief:** `skill-output/mission-brief/Mission-Brief-files-incremental-source.md` (SC-001..SC-010, DC-001/DC-002/DC-FINAL). **Locked decision:** activation is **config-only** via `source.incremental.cursor_path` (no CLI flag this round).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `python/src/chunkshop/config.py` | `FilesIncrementalSettings` model + optional `incremental` field on `FilesSource` | Modify (~line 70-74) |
| `python/src/chunkshop/sources/files.py` | `IncrementalSource` + `PrunableSource` methods, hashing, `current_paths`, shared `_document_for` | Modify |
| `python/src/chunkshop/incremental_cursor.py` | Atomic load/save of the JSON cursor sidecar | Create |
| `python/src/chunkshop/runner.py` | Incremental driver: load cursor → `iter_changes_since` → prune → save-on-success | Modify (~line 60-162) |
| `python/tests/chunkshop/test_files_incremental_config.py` | Config round-trip + `extra=forbid` + absent-default | Create |
| `python/tests/chunkshop/test_files_incremental.py` | Source-level: SC-001..SC-005 | Create |
| `python/tests/chunkshop/test_incremental_cursor.py` | Sidecar persistence unit tests | Create |
| `python/tests/chunkshop/test_runner_files_incremental.py` | Runner integration: SC-006..SC-010 (SQLite, no infra) | Create |
| `docs/incremental.md`, `docs/cookbook/incremental-sources.md` | Document the `files` incremental path | Modify |
| `docs/samples/incremental-files/` | Runnable `sample.yaml` + `run_demo.sh` + `README.md` | Create |

---

## Task 1: Config — `FilesIncrementalSettings` + `incremental` field

**Files:**
- Modify: `python/src/chunkshop/config.py:70-74`
- Test: `python/tests/chunkshop/test_files_incremental_config.py`

- [ ] **Step 1: Write the failing test**

```python
# python/tests/chunkshop/test_files_incremental_config.py
import pytest
from pydantic import ValidationError
from chunkshop.config import FilesSource


def test_files_incremental_absent_defaults_none():
    cfg = FilesSource(type="files", glob="x/*.md")
    assert cfg.incremental is None


def test_files_incremental_parses_with_defaults():
    cfg = FilesSource(
        type="files", glob="x/*.md",
        incremental={"cursor_path": ".chunkshop/cur.json"},
    )
    assert cfg.incremental.cursor_path == ".chunkshop/cur.json"
    assert cfg.incremental.detect == "hash"  # default


def test_files_incremental_detect_mtime():
    cfg = FilesSource(
        type="files", glob="x/*.md",
        incremental={"cursor_path": "c.json", "detect": "mtime"},
    )
    assert cfg.incremental.detect == "mtime"


def test_files_incremental_rejects_unknown_key():
    with pytest.raises(ValidationError):
        FilesSource(
            type="files", glob="x/*.md",
            incremental={"cursor_path": "c.json", "typo": 1},
        )


def test_files_incremental_rejects_bad_detect():
    with pytest.raises(ValidationError):
        FilesSource(
            type="files", glob="x/*.md",
            incremental={"cursor_path": "c.json", "detect": "git"},
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chunkshop/test_files_incremental_config.py -v`
Expected: FAIL — `FilesSource` has no `incremental` field (extra=forbid rejects it).

- [ ] **Step 3: Write minimal implementation**

In `python/src/chunkshop/config.py`, replace the existing `FilesSource` block (lines 70-74) with:

```python
class FilesIncrementalSettings(_Base):
    """Opt-in incremental sync for the local ``files`` source.

    When ``cursor_path`` is set, ``chunkshop ingest`` persists a JSON cursor at
    that path and on each run reprocesses only new/changed files, pruning chunks
    for files deleted from disk. Absent → full resync every run (unchanged
    behavior). ``detect`` chooses change detection: ``hash`` (default) reads each
    file and compares a sha256 of its bytes — reliable across ``git checkout``;
    ``mtime`` skips unchanged files by ``(mtime, size)`` alone without reading
    them (fast, but unreliable on git work-trees where checkout resets mtimes).
    """
    cursor_path: str
    detect: Literal["hash", "mtime"] = "hash"


class FilesSource(_Base):
    type: Literal["files"]
    glob: str
    id_from: Literal["path", "stem", "sha1"] = "stem"
    encoding: str = "utf-8"
    incremental: Optional[FilesIncrementalSettings] = None
```

(`_Base`, `Literal`, and `Optional` are already imported at the top of `config.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/chunkshop/test_files_incremental_config.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_files_incremental_config.py
git commit -m "feat(files): add optional incremental config (cursor_path, detect)"
```

---

## Task 2: `FilesSource` implements `IncrementalSource` (hash + mtime detection)

**Files:**
- Modify: `python/src/chunkshop/sources/files.py`
- Test: `python/tests/chunkshop/test_files_incremental.py`

- [ ] **Step 1: Write the failing test**

```python
# python/tests/chunkshop/test_files_incremental.py
import hashlib
import os
from pathlib import Path

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.base import IncrementalSource, SyncMode
from chunkshop.testing import (
    assert_cursor_advances, assert_idempotent_on_re_emit, merge_cursor,
)


def _cfg(tmp_path, detect="hash"):
    return Cfg(
        type="files", glob=str(tmp_path / "**" / "*.md"), id_from="path",
        incremental={"cursor_path": str(tmp_path / "cur.json"), "detect": detect},
    )


def test_files_is_incremental(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    src = FilesSource(_cfg(tmp_path))
    assert isinstance(src, IncrementalSource)
    assert src.sync_mode == SyncMode.CURSOR


def test_full_resync_from_empty_cursor_sets_fingerprint(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    src = FilesSource(_cfg(tmp_path))
    docs = sorted(src.iter_changes_since(src.empty_cursor()), key=lambda d: d.id)
    assert [d.id for d in docs] == [str(tmp_path / "a.md"), str(tmp_path / "b.md")]
    assert docs[0].fingerprint == hashlib.sha256(b"alpha").hexdigest()


def test_exact_delta_after_edit_and_add(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    src = FilesSource(_cfg(tmp_path))
    cursor = src.empty_cursor()
    first = list(src.iter_changes_since(cursor))
    cursor = merge_cursor(src, cursor, first)
    # nothing changed → no re-emit
    assert list(src.iter_changes_since(cursor)) == []
    # edit a.md, add c.md, leave b.md byte-identical
    (tmp_path / "a.md").write_text("alpha-v2")
    (tmp_path / "c.md").write_text("gamma")
    changed = list(src.iter_changes_since(cursor))
    assert {d.id for d in changed} == {str(tmp_path / "a.md"), str(tmp_path / "c.md")}


def test_mtime_mode_differs_from_hash_mode(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("alpha")
    st = f.stat()
    # --- mtime mode: rewrite content but pin mtime+size → NOT re-emitted ---
    src_m = FilesSource(_cfg(tmp_path, detect="mtime"))
    cursor = merge_cursor(src_m, src_m.empty_cursor(),
                          list(src_m.iter_changes_since(src_m.empty_cursor())))
    f.write_text("ALPHA")  # same byte length (5), different content
    os.utime(f, (st.st_atime, st.st_mtime))  # pin mtime back
    assert list(src_m.iter_changes_since(cursor)) == [], "mtime mode must skip pinned file"
    # --- hash mode: same situation → IS re-emitted ---
    src_h = FilesSource(_cfg(tmp_path, detect="hash"))
    cursor_h = merge_cursor(src_h, src_h.empty_cursor(),
                            list(src_h.iter_changes_since(src_h.empty_cursor())))
    f.write_text("OMEGA")  # length 5 again
    os.utime(f, (st.st_atime, st.st_mtime))
    assert [d.id for d in src_h.iter_changes_since(cursor_h)] == [str(f)], \
        "hash mode must catch a content change even with pinned mtime"


def test_passes_shared_incremental_contracts(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    src = FilesSource(_cfg(tmp_path))
    assert_cursor_advances(src)
    assert_idempotent_on_re_emit(src)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chunkshop/test_files_incremental.py -v`
Expected: FAIL — `FilesSource` has no `iter_changes_since` / `sync_mode` / `empty_cursor`.

- [ ] **Step 3: Write minimal implementation**

Rewrite `python/src/chunkshop/sources/files.py` to add the protocol methods and a shared `_document_for`. Replace the import line and `iter_documents`, and append the new methods:

```python
from __future__ import annotations

import glob as _glob
import hashlib
from pathlib import Path
from typing import Iterator, Optional

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.base import Document, SyncMode
from chunkshop.sources.parsers import DEFAULT_PARSERS, FileParser, get_parser
from chunkshop.sources.parsers.text import TextParser


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FilesSource:
    """Glob-driven file loader. Dispatches by extension to a `FileParser`.

    Implements ``IncrementalSource`` (and ``PrunableSource`` — see below): the
    cursor is a ``{path: {"h": sha256, "mt": mtime, "sz": size}}`` map. ``hash``
    detection (default) compares the content hash; ``mtime`` detection trusts
    ``(mtime, size)`` and never reads unchanged files. ``sync_mode`` is always
    ``CURSOR`` — the source *can* sync incrementally; whether the CLI does is
    gated by ``cfg.incremental`` in the runner.
    """

    # Extensions that should respect ``cfg.encoding`` for backward compat
    # when the caller didn't pass an explicit `parsers=` map.
    _TEXT_EXTS = ("txt", "md", "markdown", "rst", "log", "csv", "tsv", "")

    sync_mode = SyncMode.CURSOR

    def __init__(
        self,
        cfg: Cfg,
        parsers: Optional[dict[str, FileParser]] = None,
    ):
        self.cfg = cfg
        self._detect = cfg.incremental.detect if cfg.incremental else "hash"
        if parsers is None:
            text_parser = TextParser(encoding=cfg.encoding)
            self._parsers: dict[str, FileParser] = dict(DEFAULT_PARSERS)
            for ext in self._TEXT_EXTS:
                self._parsers[ext] = text_parser
        else:
            self._parsers = dict(parsers)

    # --- full-resync path (unchanged behavior) ---------------------------

    def iter_documents(self) -> Iterator[Document]:
        paths = self.current_paths()
        if not paths:
            raise ValueError(f"no files matched glob: {self.cfg.glob}")
        for p in paths:
            yield self._document_for(Path(p))

    # --- IncrementalSource ------------------------------------------------

    def current_paths(self) -> list[str]:
        """Sorted list of paths currently matching the glob — no file reads."""
        return sorted(_glob.glob(self.cfg.glob, recursive=True))

    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterator[Document]:
        # NOTE: unlike iter_documents, an empty match set is NOT an error here —
        # on a non-first run it legitimately means every file was deleted; the
        # runner's prune step handles that.
        for p in self.current_paths():
            path = Path(p)
            prev = cursor.get(p)
            st = path.stat()
            if (
                self._detect == "mtime"
                and prev is not None
                and prev.get("mt") == st.st_mtime
                and prev.get("sz") == st.st_size
            ):
                continue  # unchanged by stat — skip without reading bytes
            content_hash = _hash_bytes(path.read_bytes())
            if prev is not None and prev.get("h") == content_hash:
                continue  # unchanged content
            yield self._document_for(path, content_hash)

    def cursor_from(self, last_document: Document) -> dict:
        meta = last_document.metadata or {}
        p = meta.get("source_path", last_document.id)
        st = Path(p).stat()
        return {p: {"h": last_document.fingerprint, "mt": st.st_mtime, "sz": st.st_size}}

    # --- PrunableSource (implemented in Task 3) ---------------------------

    # --- shared helpers ---------------------------------------------------

    def _document_for(self, path: Path, content_hash: Optional[str] = None) -> Document:
        ext = path.suffix.lower().lstrip(".")
        parser = get_parser(ext, self._parsers)
        text = parser.parse(path)
        return Document(
            id=self._id_for(path),
            content=text,
            title=path.name,
            metadata={
                "source_path": str(path),
                "parser": parser.__class__.__name__,
            },
            fingerprint=content_hash,
        )

    def _id_for(self, path: Path) -> str:
        mode = self.cfg.id_from
        if mode == "path":
            return str(path)
        if mode == "stem":
            return path.stem
        if mode == "sha1":
            return hashlib.sha1(str(path).encode()).hexdigest()
        raise ValueError(mode)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/chunkshop/test_files_incremental.py tests/chunkshop/test_sources_files.py -v`
Expected: PASS — new incremental tests pass AND the existing `test_sources_files.py` still passes (full-resync behavior unchanged; `_document_for` produces the same id/content/title, now with `fingerprint=None`).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sources/files.py python/tests/chunkshop/test_files_incremental.py
git commit -m "feat(files): implement IncrementalSource (hash + mtime detection)"
```

---

## Task 3: `FilesSource` implements `PrunableSource`

**Files:**
- Modify: `python/src/chunkshop/sources/files.py` (add two methods in the marked section)
- Test: `python/tests/chunkshop/test_files_incremental.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `python/tests/chunkshop/test_files_incremental.py`:

```python
from chunkshop.sources.base import PrunableSource


def test_files_is_prunable(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    src = FilesSource(_cfg(tmp_path))
    assert isinstance(src, PrunableSource)


def test_iter_deleted_since_reports_removed_doc_ids(tmp_path):
    a = tmp_path / "a.md"; a.write_text("alpha")
    b = tmp_path / "b.md"; b.write_text("beta")
    src = FilesSource(_cfg(tmp_path))  # id_from="path" → doc_id == str(path)
    cursor = merge_cursor(src, src.empty_cursor(),
                          list(src.iter_changes_since(src.empty_cursor())))
    # nothing deleted yet
    assert list(src.iter_deleted_since(cursor)) == []
    # delete b.md on disk
    b.unlink()
    deleted = list(src.iter_deleted_since(cursor))
    assert deleted == [str(b)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chunkshop/test_files_incremental.py -k "prunable or deleted" -v`
Expected: FAIL — `FilesSource` has no `iter_deleted_since` / `empty_prune_cursor`.

- [ ] **Step 3: Write minimal implementation**

In `python/src/chunkshop/sources/files.py`, replace the `# --- PrunableSource (implemented in Task 3) ---` comment block with:

```python
    # --- PrunableSource ---------------------------------------------------

    def empty_prune_cursor(self) -> dict:
        return {}

    def iter_deleted_since(self, cursor: dict) -> Iterator[str]:
        """Yield doc_ids for files present in ``cursor`` but absent on disk.

        The cursor is keyed by path; deletion IDs are the ``Document.id`` values
        (via ``_id_for``), so the consumer can pass them straight to
        ``sink.delete_document``.
        """
        current = set(self.current_paths())
        for p in cursor:
            if p not in current:
                yield self._id_for(Path(p))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/chunkshop/test_files_incremental.py -v`
Expected: PASS (all source-level tests, SC-001..SC-005)

⛔ **Drift Check DC-001:** Re-read `skill-output/mission-brief/Mission-Brief-files-incremental-source.md`. Verify SC-001..SC-005 have passing tests and that the implementation mirrors `sources/s3.py` without modifying the `IncrementalSource`/`PrunableSource` protocol definitions in `sources/base.py`. If misaligned, stop and reassess.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sources/files.py python/tests/chunkshop/test_files_incremental.py
git commit -m "feat(files): implement PrunableSource (detect on-disk deletions)"
```

---

## Task 4: Atomic cursor sidecar (`incremental_cursor.py`)

**Files:**
- Create: `python/src/chunkshop/incremental_cursor.py`
- Test: `python/tests/chunkshop/test_incremental_cursor.py`

- [ ] **Step 1: Write the failing test**

```python
# python/tests/chunkshop/test_incremental_cursor.py
from pathlib import Path
from chunkshop.incremental_cursor import load_cursor, save_cursor_atomic


def test_load_missing_returns_empty(tmp_path):
    assert load_cursor(tmp_path / "nope.json") == {}


def test_save_then_load_round_trip(tmp_path):
    p = tmp_path / "sub" / "cur.json"  # parent does not exist yet
    cursor = {"a.md": {"h": "deadbeef", "mt": 1.0, "sz": 5}}
    save_cursor_atomic(p, cursor)
    assert p.exists()
    assert load_cursor(p) == cursor


def test_save_leaves_no_tmp_file(tmp_path):
    p = tmp_path / "cur.json"
    save_cursor_atomic(p, {"x": 1})
    assert list(tmp_path.glob("*.tmp")) == []


def test_existing_cursor_intact_until_replace(tmp_path):
    # Writing a NEW cursor must not clobber the old one until the atomic replace.
    p = tmp_path / "cur.json"
    save_cursor_atomic(p, {"v": 1})
    assert load_cursor(p) == {"v": 1}
    save_cursor_atomic(p, {"v": 2})
    assert load_cursor(p) == {"v": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chunkshop/test_incremental_cursor.py -v`
Expected: FAIL — module `chunkshop.incremental_cursor` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# python/src/chunkshop/incremental_cursor.py
"""Atomic JSON persistence for the local ``files`` source's incremental cursor.

The cursor is an opaque ``{path: {"h", "mt", "sz"}}`` dict (see
``chunkshop.sources.files.FilesSource``). Writes go to a temp file in the same
directory, then ``os.replace`` swaps it in — atomic on POSIX, so a crash mid-write
never corrupts an existing cursor. chunkshop only writes the cursor on a fully
successful run (see ``runner.run_cell``); a failed run leaves the prior cursor
untouched and the next run safely re-ingests via idempotent upsert.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def load_cursor(path: str | os.PathLike) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_cursor_atomic(path: str | os.PathLike, cursor: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cursor, f)
    os.replace(tmp, p)  # atomic swap on POSIX
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/chunkshop/test_incremental_cursor.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/incremental_cursor.py python/tests/chunkshop/test_incremental_cursor.py
git commit -m "feat: add atomic incremental cursor sidecar store"
```

---

## Task 5: Runner drives the CLI sidecar (skip + prune + save-on-success)

**Files:**
- Modify: `python/src/chunkshop/runner.py` (imports; `run_cell` body ~line 60-162)
- Test: `python/tests/chunkshop/test_runner_files_incremental.py`

- [ ] **Step 1: Write the failing test**

```python
# python/tests/chunkshop/test_runner_files_incremental.py
import textwrap
from pathlib import Path

import pytest

from chunkshop.config import load_config
from chunkshop.runner import run_cell
from chunkshop.incremental_cursor import load_cursor


def _write_cell(tmp_path, corpus_dir, cursor_path, *, incremental=True,
                glob_suffix="*.md", chunker="sentence_aware"):
    """Build a files→sqlite cell YAML. Explicit line list (NOT textwrap.dedent)
    so the conditional incremental block nests correctly — splicing a multiline
    value into a dedented f-string mis-indents the continuation lines."""
    lines = [
        "cell_name: files_inc",
        "source:",
        "  type: files",
        f"  glob: {corpus_dir}/**/{glob_suffix}",
        "  id_from: path",
    ]
    if incremental:
        lines += [
            "  incremental:",
            f"    cursor_path: {cursor_path}",
            "    detect: hash",
        ]
    lines += [
        "chunker:",
        f"  type: {chunker}",
        "embedder:",
        "  type: fastembed",
        "  model_name: BAAI/bge-small-en-v1.5",
        "  dim: 384",
        "target:",
        "  type: sqlite",
        f"  dsn: {tmp_path / 'vecs.db'}",
        "  database: main",
        "  table: files_inc",
        "  mode: create_if_missing",
        "  source_tag: files_inc",
        "  hnsw: false",
        "runtime:",
        "  omp_num_threads: 1",
    ]
    yaml_path = tmp_path / "cell.yaml"
    yaml_path.write_text("\n".join(lines) + "\n")
    return load_config(yaml_path)


def test_unchanged_rerun_processes_zero_docs(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("The first document about cats.")
    (corpus / "b.md").write_text("The second document about dogs.")
    cursor = tmp_path / "cur.json"
    cfg = _write_cell(tmp_path, corpus, cursor)

    r1 = run_cell(cfg)
    assert r1.error is None
    assert r1.docs_processed == 2 and r1.chunks_written >= 2
    assert load_cursor(cursor).keys() == {str(corpus / "a.md"), str(corpus / "b.md")}

    r2 = run_cell(cfg)  # nothing changed
    assert r2.error is None
    assert r2.docs_processed == 0 and r2.chunks_written == 0

    (corpus / "a.md").write_text("The first document, now about elephants.")
    r3 = run_cell(cfg)  # only a.md changed
    assert r3.error is None
    assert r3.docs_processed == 1


def test_deleted_file_is_pruned(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("alpha document about cats")
    (corpus / "b.md").write_text("beta document about dogs")
    cursor = tmp_path / "cur.json"
    cfg = _write_cell(tmp_path, corpus, cursor)
    run_cell(cfg)

    from chunkshop.sinks import load_sink
    sink = load_sink(cfg.target, embed_dim=cfg.embedder.dim)
    assert sink.count_docs() == 2

    (corpus / "b.md").unlink()
    r = run_cell(cfg)
    assert r.error is None
    sink2 = load_sink(cfg.target, embed_dim=cfg.embedder.dim)
    assert sink2.count_docs() == 1  # b.md's chunks pruned
    assert str(corpus / "b.md") not in load_cursor(cursor)


def test_no_incremental_block_is_full_resync_and_writes_no_cursor(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("alpha document about cats")
    cfg = _write_cell(tmp_path, corpus, tmp_path / "cur.json", incremental=False)
    r1 = run_cell(cfg)
    r2 = run_cell(cfg)
    assert r1.docs_processed == 1 and r2.docs_processed == 1  # full resync each run
    assert not (tmp_path / "cur.json").exists()  # no sidecar created


def test_crash_mid_run_leaves_prior_cursor_intact(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("alpha about cats")
    (corpus / "b.md").write_text("beta about dogs")
    cursor = tmp_path / "cur.json"
    cfg = _write_cell(tmp_path, corpus, cursor)
    run_cell(cfg)
    saved = load_cursor(cursor)

    # Edit both files so run 2 re-emits both, then fail on the 2nd write.
    (corpus / "a.md").write_text("alpha about cats v2")
    (corpus / "b.md").write_text("beta about dogs v2")

    import chunkshop.sinks.sqlite as sq
    calls = {"n": 0}
    real = sq.SqliteSink.write_document
    def boom(self, doc_id, chunks, embeddings, tags):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated mid-run crash")
        return real(self, doc_id, chunks, embeddings, tags)
    monkeypatch.setattr(sq.SqliteSink, "write_document", boom)

    r = run_cell(cfg)
    assert r.error is not None                  # runner caught the crash
    assert load_cursor(cursor) == saved         # cursor NOT advanced

    monkeypatch.undo()
    r2 = run_cell(cfg)                           # clean retry
    assert r2.error is None
    from chunkshop.sinks import load_sink
    assert load_sink(cfg.target, embed_dim=cfg.embedder.dim).count_docs() == 2  # no dup docs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/chunkshop/test_runner_files_incremental.py -v`
Expected: FAIL — runner ignores `cfg.source.incremental`; run 2 still processes 2 docs and no cursor file is written.

- [ ] **Step 3: Write minimal implementation**

In `python/src/chunkshop/runner.py`:

(a) Add to the imports block (after line 19, `from chunkshop.sources import load_source`):

```python
from chunkshop.sources.base import IncrementalSource, PrunableSource
```

(b) After `source = load_source(cfg.source)` (line 62), add the incremental setup:

```python
        inc = getattr(cfg.source, "incremental", None)
        incremental = bool(inc and getattr(inc, "cursor_path", None)) and isinstance(
            source, IncrementalSource
        )
        if incremental:
            from chunkshop.incremental_cursor import load_cursor, save_cursor_atomic

            cursor = load_cursor(inc.cursor_path)
            new_cursor = dict(cursor)
            doc_source = source.iter_changes_since(cursor)
            _log(f"incremental: loaded cursor ({len(cursor)} entries)", log_path)
        else:
            doc_source = source.iter_documents()
```

(c) Change the loop header (line 87) from `for raw in source.iter_documents():` to:

```python
        for raw in doc_source:
```

(d) Record the per-doc cursor delta. Immediately after `docs_processed += 1` (line 154), add:

```python
            if incremental:
                new_cursor.update(source.cursor_from(raw))
```

(e) Prune + save, on the success path only. Insert immediately BEFORE the `finalize = getattr(extractor, "finalize", None)` line (line 168):

```python
        if incremental:
            if isinstance(source, PrunableSource):
                deleted = list(source.iter_deleted_since(cursor))
                for did in deleted:
                    sink.delete_document(did)
                if deleted:
                    _log(f"incremental: pruned {len(deleted)} deleted docs", log_path)
            # Trim the cursor to the current on-disk manifest so deleted paths
            # drop out (current_paths is files-source-specific; guard for it).
            current_paths = getattr(source, "current_paths", None)
            if current_paths is not None:
                keep = set(current_paths())
                new_cursor = {p: e for p, e in new_cursor.items() if p in keep}
            save_cursor_atomic(inc.cursor_path, new_cursor)
            _log(f"incremental: saved cursor ({len(new_cursor)} entries)", log_path)
```

Because this block sits inside the `try` and after the document loop, any exception during the loop jumps to the `except` handler (line 227) and the cursor is never saved — satisfying crash-safety (SC-008).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/chunkshop/test_runner_files_incremental.py -v`
Expected: PASS (4 passed). First run downloads/caches the fastembed model — allow time.

⛔ **Drift Check DC-002:** Re-read the mission brief. Verify SC-006 (zero-doc unchanged re-run), SC-007 (prune), and SC-008 (crash-safe cursor) pass, and re-confirm SC-009 (the no-`incremental` cell is full-resync and writes no cursor file). If misaligned, stop and reassess.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/runner.py python/tests/chunkshop/test_runner_files_incremental.py
git commit -m "feat(runner): drive files incremental sidecar (skip, prune, save-on-success)"
```

---

## Task 6: Code-corpus parity (SC-010)

**Files:**
- Test: `python/tests/chunkshop/test_runner_files_incremental.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `python/tests/chunkshop/test_runner_files_incremental.py`:

```python
def test_code_corpus_skips_unchanged_py_files(tmp_path):
    # Proves the cursor is content-agnostic: a .py corpus with a code chunker
    # behaves exactly like the prose corpus. code_aware uses the stdlib AST
    # (no [code] extra needed); symbol_aware behaves identically.
    corpus = tmp_path / "src"; corpus.mkdir()
    (corpus / "mod_a.py").write_text("def alpha():\n    return 1\n")
    (corpus / "mod_b.py").write_text("def beta():\n    return 2\n")
    cursor = tmp_path / "cur.json"
    cfg = _write_cell(tmp_path, corpus, cursor,
                      glob_suffix="*.py", chunker="code_aware")

    r1 = run_cell(cfg)
    assert r1.error is None and r1.docs_processed == 2
    r2 = run_cell(cfg)
    assert r2.error is None and r2.docs_processed == 0  # unchanged → skipped

    (corpus / "mod_a.py").write_text("def alpha():\n    return 42\n")
    r3 = run_cell(cfg)
    assert r3.error is None and r3.docs_processed == 1
```

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `uv run pytest tests/chunkshop/test_runner_files_incremental.py::test_code_corpus_skips_unchanged_py_files -v`
Expected: PASS immediately — Task 5 already implemented the behavior; this test asserts the "code and files" requirement explicitly. (If it fails, the cursor is accidentally coupled to chunker/content type — fix before proceeding.)

- [ ] **Step 3: (no new implementation)** — this task is a coverage gate, not new code.

- [ ] **Step 4: Run the full source + runner suite**

Run: `uv run pytest tests/chunkshop/test_files_incremental.py tests/chunkshop/test_files_incremental_config.py tests/chunkshop/test_incremental_cursor.py tests/chunkshop/test_runner_files_incremental.py tests/chunkshop/test_sources_files.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add python/tests/chunkshop/test_runner_files_incremental.py
git commit -m "test(files): assert code-corpus incremental parity (SC-010)"
```

---

## Task 7: Documentation + runnable sample

**Files:**
- Modify: `docs/incremental.md`, `docs/cookbook/incremental-sources.md`
- Create: `docs/samples/incremental-files/sample.yaml`, `docs/samples/incremental-files/run_demo.sh`, `docs/samples/incremental-files/README.md`

- [ ] **Step 1: Add the sample config**

Create `docs/samples/incremental-files/sample.yaml`:

```yaml
# Local directory, incremental: only new/changed files are reprocessed; files
# deleted from disk have their chunks pruned. Works for prose AND source code.
cell_name: files_incremental
source:
  type: files
  glob: ./corpus/**/*.md
  id_from: path
  incremental:
    cursor_path: ./.chunkshop/files-cursor.json
    detect: hash          # default; survives git checkout. `mtime` = stat-only fast-path.
chunker:
  type: hierarchy
  max_chars: 1200
embedder:
  type: fastembed
  model_name: "Xenova/bge-small-en-v1.5-int8"
  dim: 384
target:
  dsn_env: VECTORS_DB_DSN
  schema: rag
  table: notes_chunks
  mode: create_if_missing
  source_tag: files_incremental
  delete_orphans: true
```

- [ ] **Step 2: Add the demo script**

Create `docs/samples/incremental-files/run_demo.sh`:

```bash
#!/usr/bin/env bash
# Demonstrates files-source incremental: ingest a dir, edit + delete a file,
# re-ingest, and show that only the changed file is reprocessed.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p corpus
echo "First note about cats." > corpus/a.md
echo "Second note about dogs." > corpus/b.md

echo "=== run 1: full ingest (expect docs_processed=2) ==="
chunkshop ingest --config sample.yaml

echo "=== run 2: no changes (expect docs_processed=0) ==="
chunkshop ingest --config sample.yaml

echo "=== edit a.md, delete b.md ==="
echo "First note, now about elephants." > corpus/a.md
rm corpus/b.md

echo "=== run 3: 1 changed + 1 deleted (expect docs_processed=1, b.md pruned) ==="
chunkshop ingest --config sample.yaml
echo "cursor:"; cat .chunkshop/files-cursor.json
```

Then: `chmod +x docs/samples/incremental-files/run_demo.sh`

- [ ] **Step 3: Add the sample README**

Create `docs/samples/incremental-files/README.md`:

```markdown
# Incremental local files

Point the `files` source at a directory and reprocess only what changed.

- **Cursor:** a JSON file at `source.incremental.cursor_path` mapping each file
  path → `{hash, mtime, size}`. chunkshop writes it only after a fully
  successful run (atomic temp-file + rename), so a crash leaves the prior cursor
  intact and the next run safely re-upserts.
- **Detection:** `detect: hash` (default) compares a sha256 of file bytes —
  reliable across `git checkout`. `detect: mtime` skips unchanged files by
  `(mtime, size)` without reading them (faster, but unreliable on git work-trees).
- **Deletions:** files removed from disk have their chunks pruned (scoped to the
  cell's `source_tag`).
- **Code or prose:** identical behavior — local source code ingests through this
  same source (`type: files` + a code chunker like `symbol_aware`/`code_aware`).

Run `./run_demo.sh` (needs `$VECTORS_DB_DSN`) to see a 3-run delta walkthrough.
```

- [ ] **Step 4: Update the reference docs**

In `docs/cookbook/incremental-sources.md`, after the HTTP cursor subsection (around line 162, before "## Stale cursors"), add:

```markdown
### Local files: `{path: {h, mt, sz}}` cursor + CLI sidecar (`chunkshop.sources.files.FilesSource`)

`sync_mode = SyncMode.CURSOR`. The cursor maps each matched file path to a
content hash, mtime, and size. `iter_changes_since(cursor)` yields only files
whose hash differs (default) — or, with `detect: mtime`, whose `(mtime, size)`
differs without reading the file. `FilesSource` also implements `PrunableSource`:
`iter_deleted_since(cursor)` returns the doc_ids of files in the cursor that are
gone from disk.

Unlike `s3`/`http`/`pg_table`, the `files` source can be driven **by the CLI
itself** — set `source.incremental.cursor_path` and `chunkshop ingest` loads the
cursor, skips unchanged files, prunes deletions, and rewrites the cursor on
success. No external consumer loop required. See
[`docs/samples/incremental-files/`](../samples/incremental-files/).
```

Also update the `SyncMode` table note in this file (line 21) and add a row for `files` to the cursor-shapes overview as appropriate.

In `docs/incremental.md`, update **Pattern C** (the staging-file inbox, line 208) with a note that the `files` source is now natively incremental, and add `files` to the "Reference: the relevant config knobs" block (line 569) showing the `incremental:` sub-config. Change the TL;DR line that says "You bring the scheduler and the change-detector" to note the `files` source can now self-detect changes via its cursor sidecar (the scheduler is still yours).

- [ ] **Step 5: Commit**

```bash
git add docs/incremental.md docs/cookbook/incremental-sources.md docs/samples/incremental-files/
git commit -m "docs(files): document incremental files source + runnable sample"
```

---

## Task 8: Full-suite verification + final drift gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full Python suite**

Run: `uv run --no-sync pytest -q`
Expected: All pass (no regressions). If any pre-existing tests reference the `files` source's `sync_mode` as `FULL_RESYNC`, update them — `files` is now `CURSOR`.

- [ ] **Step 2: Run the demo end-to-end (optional, needs a DSN)**

Run: `cd docs/samples/incremental-files && VECTORS_DB_DSN=... ./run_demo.sh`
Expected: run 1 → `docs_processed=2`; run 2 → `docs_processed=0`; run 3 → `docs_processed=1` and the cursor no longer lists `b.md`.

- [ ] **Step 3: ⛔ Drift Check DC-FINAL**

Re-read `skill-output/mission-brief/Mission-Brief-files-incremental-source.md`. Confirm every criterion has evidence:

| SC | Evidence |
|---|---|
| SC-001 | `test_files_is_incremental`, `test_files_is_prunable` |
| SC-002 | `test_full_resync_from_empty_cursor_sets_fingerprint` |
| SC-003 | `test_exact_delta_after_edit_and_add` |
| SC-004 | `test_mtime_mode_differs_from_hash_mode` |
| SC-005 | `test_iter_deleted_since_reports_removed_doc_ids` |
| SC-006 | `test_unchanged_rerun_processes_zero_docs` |
| SC-007 | `test_deleted_file_is_pruned` |
| SC-008 | `test_crash_mid_run_leaves_prior_cursor_intact` |
| SC-009 | `test_no_incremental_block_is_full_resync_and_writes_no_cursor` |
| SC-010 | `test_code_corpus_skips_unchanged_py_files` |

Confirm constraints hold: no new runtime deps (stdlib only); `incremental` absent → unchanged behavior; protocols in `sources/base.py` unmodified; cursor writes atomic + advance-on-success; prune scoped to `source_tag`; docs updated. If any SC lacks evidence, that work is not complete.

- [ ] **Step 4: Commit any verification fixes**

```bash
git add -A
git commit -m "test: files incremental — full-suite green, DC-FINAL verified"
```

---

## Self-Review (completed during planning)

**Spec coverage:** SC-001..SC-010 each map to a named test (DC-FINAL table). Constraints (no new deps, additive config, unmodified protocols, atomic/advance-on-success cursor, source_tag-scoped prune, Python-only) are enforced in Tasks 1-5 and re-checked at DC-FINAL.

**Out-of-scope honored:** no Rust changes; `comment_extracts` untouched; no `detect: git`; no scheduler/daemon; no CLI flag (config-only per locked decision); no cursor locking; existing configs unchanged (SC-009).

**Type consistency:** cursor entry shape `{"h", "mt", "sz"}` is identical across `iter_changes_since`, `cursor_from`, and the runner trim. `current_paths()`, `iter_deleted_since()`, `_document_for()`, `_id_for()` names are consistent across Tasks 2-6. Sink method `delete_document(doc_id)` matches `sinks/sqlite.py:254` and `sinks/pg.py:499`. `save_cursor_atomic`/`load_cursor` names match between Task 4 and Task 5.

**Known minor concern (logged, not blocking):** `cursor_from` re-stats the file rather than reusing the stat from `iter_changes_since`; a file changed in the sub-second window between emit and `cursor_from` could store a slightly newer mtime. Harmless for a batch worker (worst case: one extra reprocess next run). `detect: hash` reads file bytes twice for changed files (once to hash, once to parse) — acceptable since the expensive chunk+embed step is what's being saved.
