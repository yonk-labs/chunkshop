# chunkshop Schema Flexibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve chunkshop from one-cell-per-table to multi-cell-into-one-table with append mode, a `source` discriminator column, YAML-declared jsonb-to-column promotion, and an extractor contract that returns structured `(tags, metadata)`.

**Architecture:** Expand `TargetConfig` pydantic model with `mode`, `source_tag`, `promote_metadata`, and `force_overwrite` fields. Refactor `PgVectorSink.create_table` into mode-aware behavior with pre-flight schema checks. Extend the extractor `Protocol` to return an `ExtractResult(tags, metadata)` dataclass; migrate existing extractors; merge extracted metadata into each chunk's metadata dict before sink write. No new runtime dependencies.

**Tech Stack:** Python 3.12, pydantic v2, psycopg 3, pgvector, pytest.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-schema-flexibility.md`. This plan implements all 10 Success Criteria (SC-001…SC-010). Drift Checkpoints (DC-001…DC-FINAL) are injected as ⛔ hard gates between task groups.

---

## Prerequisites

- Local Postgres (with `pgvector` extension) reachable at `$CHUNKSHOP_TEST_DSN` or the default `postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg`. DB-touching tests skip if unreachable.
- `cd chunkshop/python && uv sync --extra dev` completed. Active venv or `uv run` prefix.

## File Structure

**New files:**
- `python/src/chunkshop/extractors/result.py` — `ExtractResult` dataclass.
- `python/tests/chunkshop/test_config_target_flexibility.py` — unit tests for new `TargetConfig` fields.
- `python/tests/chunkshop/test_sink_append_mode.py` — integration tests for append/overwrite safety.
- `python/tests/chunkshop/test_multi_source_ingest.py` — two-cell integration test.
- `docs/tutorial-multi-source.md` — narrative walkthrough (SC-009).
- `docs/quickstart-multi-source.md` — copy-paste quickstart (SC-010).

**Modified files:**
- `python/src/chunkshop/extractors/base.py` — update `Extractor` Protocol return type.
- `python/src/chunkshop/extractors/none_provider.py` — return `ExtractResult`.
- `python/src/chunkshop/extractors/rake_keywords.py` — return `ExtractResult`.
- `python/src/chunkshop/extractors/__init__.py` — re-export `ExtractResult`.
- `python/src/chunkshop/runner.py` — handle `ExtractResult`, merge metadata into chunks.
- `python/src/chunkshop/config.py` — extend `TargetConfig`; add `PromoteColumn` model.
- `python/src/chunkshop/sink.py` — mode-aware create_table, pre-flight, source + promoted columns.
- `python/tests/chunkshop/test_extractor_rake.py` — update to new contract.

---

## Task 1: Read current state, note before-migration behavior

**Files:** (read-only)

- [ ] **Step 1: Confirm test DB reachability**

Run: `psql postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg -c "SELECT 1"` (or set `CHUNKSHOP_TEST_DSN`).
Expected: `1` row returned, or skip future integration tests if unreachable.

- [ ] **Step 2: Run the existing test suite as baseline**

Run: `cd python && uv run pytest -q`
Expected: all tests pass (integration tests may skip without Postgres). Note the count — every later task must not regress this count minus the old test we're updating.

- [ ] **Step 3: Note current extractor contract**

Read: `python/src/chunkshop/extractors/base.py`, `none_provider.py`, `rake_keywords.py`, `__init__.py`.
Current: `extract(text) -> list[str]`. Migration will change this.

- [ ] **Step 4: Note current TargetConfig**

Read: `python/src/chunkshop/config.py` — find `TargetConfig`. Current fields: `dsn_env`, `schema_name` (aliased `schema`), `table`, `overwrite`, `hnsw`. The validator `_safe_ident` enforces `^[a-z_][a-z0-9_]*$`.

---

## Task 2: Introduce `ExtractResult` dataclass

**Files:**
- Create: `python/src/chunkshop/extractors/result.py`
- Test: inline in later tasks

- [ ] **Step 1: Create `ExtractResult`**

File `python/src/chunkshop/extractors/result.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractResult:
    """Return value of an Extractor. `tags` is a flat list for the text[] column;
    `metadata` is a dict merged into each chunk's metadata jsonb.
    """
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
```

- [ ] **Step 2: Smoke-check the import**

Run: `cd python && uv run python -c "from chunkshop.extractors.result import ExtractResult; r = ExtractResult(); assert r.tags == [] and r.metadata == {}; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add python/src/chunkshop/extractors/result.py
git commit -m "feat(extractors): add ExtractResult dataclass (schema-flex SC-005 prep)"
```

---

## Task 3: Update `Extractor` Protocol return type

**Files:**
- Modify: `python/src/chunkshop/extractors/base.py`

- [ ] **Step 1: Update the Protocol**

Replace file contents:

```python
from __future__ import annotations
from typing import Protocol

from chunkshop.extractors.result import ExtractResult


class Extractor(Protocol):
    def extract(self, text: str) -> ExtractResult: ...
```

- [ ] **Step 2: Verify chunkshop still imports cleanly (other modules will break — that's expected)**

Run: `cd python && uv run python -c "from chunkshop.extractors.base import Extractor; print('Protocol ok')"`
Expected: `Protocol ok`. (Downstream tests will fail until Task 4–5 migrate.)

- [ ] **Step 3: Commit**

```bash
git add python/src/chunkshop/extractors/base.py
git commit -m "feat(extractors): change Extractor Protocol to return ExtractResult"
```

---

## Task 4: Migrate `NoneExtractor`

**Files:**
- Modify: `python/src/chunkshop/extractors/none_provider.py`

- [ ] **Step 1: Update the existing test to new contract**

File `python/tests/chunkshop/test_extractor_rake.py` — replace `test_none_returns_empty`:

```python
def test_none_returns_empty():
    extractor = load_extractor(NoneExtractor())
    result = extractor.extract("any text")
    assert result.tags == []
    assert result.metadata == {}
```

Add import at top: `from chunkshop.extractors.result import ExtractResult` (even if only used by downstream tests — keeps import sorted).

- [ ] **Step 2: Run the test — expect failure**

Run: `cd python && uv run pytest tests/chunkshop/test_extractor_rake.py::test_none_returns_empty -v`
Expected: FAIL. Error mentions `AttributeError: 'list' object has no attribute 'tags'` (or similar — proves the contract is wrong).

- [ ] **Step 3: Update `NoneExtractor`**

Replace `python/src/chunkshop/extractors/none_provider.py`:

```python
from chunkshop.config import NoneExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class NoneExtractor:
    def __init__(self, cfg: Cfg | None = None):
        self.cfg = cfg

    def extract(self, text: str) -> ExtractResult:
        return ExtractResult()
```

- [ ] **Step 4: Re-run test — expect pass**

Run: `cd python && uv run pytest tests/chunkshop/test_extractor_rake.py::test_none_returns_empty -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/extractors/none_provider.py python/tests/chunkshop/test_extractor_rake.py
git commit -m "refactor(extractors): NoneExtractor returns ExtractResult"
```

---

## Task 5: Migrate `RakeKeywordsExtractor`

**Files:**
- Modify: `python/src/chunkshop/extractors/rake_keywords.py`
- Modify: `python/tests/chunkshop/test_extractor_rake.py`

- [ ] **Step 1: Update the RAKE test**

Replace `test_rake_returns_keywords_sorted` in `python/tests/chunkshop/test_extractor_rake.py`:

```python
def test_rake_returns_keywords_sorted():
    extractor = load_extractor(RakeKeywordsExtractor(type="rake_keywords", top_k=3))
    text = (
        "Supreme Court justice Neil Gorsuch wrote the majority opinion in "
        "Bostock v. Clayton County. Bostock concerns civil rights and Title VII."
    )
    result = extractor.extract(text)
    assert isinstance(result.tags, list)
    assert 1 <= len(result.tags) <= 3
    assert result.metadata == {}  # RAKE carries no structured metadata
    lowered = [t.lower() for t in result.tags]
    assert any(
        "bostock" in t or "gorsuch" in t or "civil rights" in t or "title vii" in t
        for t in lowered
    )
```

- [ ] **Step 2: Run — expect failure**

Run: `cd python && uv run pytest tests/chunkshop/test_extractor_rake.py::test_rake_returns_keywords_sorted -v`
Expected: FAIL.

- [ ] **Step 3: Update `RakeKeywordsExtractor`**

Replace the `extract` method in `python/src/chunkshop/extractors/rake_keywords.py`:

```python
def extract(self, text: str) -> ExtractResult:
    from chunkshop.extractors.result import ExtractResult
    r = self._rake_cls(min_length=1)
    r.extract_keywords_from_text(text)
    ranked = r.get_ranked_phrases()
    tags = [p for p in ranked if len(p) >= self.cfg.min_chars][: self.cfg.top_k]
    return ExtractResult(tags=tags, metadata={})
```

Also update the return type annotation on the method signature. Add a top-level import `from chunkshop.extractors.result import ExtractResult` (replace the inline import — inline is a relic of Task 5's draft).

- [ ] **Step 4: Re-run — expect pass**

Run: `cd python && uv run pytest tests/chunkshop/test_extractor_rake.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/extractors/rake_keywords.py python/tests/chunkshop/test_extractor_rake.py
git commit -m "refactor(extractors): RakeKeywords returns ExtractResult"
```

---

## Task 6: Re-export `ExtractResult` from extractors package

**Files:**
- Modify: `python/src/chunkshop/extractors/__init__.py`

- [ ] **Step 1: Update `__init__.py`**

Replace:

```python
from chunkshop.config import (
    ExtractorConfig,
    NoneExtractor as NoneCfg,
    RakeKeywordsExtractor as RakeCfg,
)
from chunkshop.extractors.base import Extractor
from chunkshop.extractors.none_provider import NoneExtractor
from chunkshop.extractors.rake_keywords import RakeKeywordsExtractor
from chunkshop.extractors.result import ExtractResult


def load_extractor(cfg: ExtractorConfig) -> Extractor:
    if isinstance(cfg, NoneCfg):
        return NoneExtractor(cfg)
    if isinstance(cfg, RakeCfg):
        return RakeKeywordsExtractor(cfg)
    raise ValueError(f"unknown extractor type: {type(cfg).__name__}")


__all__ = ["Extractor", "ExtractResult", "load_extractor"]
```

- [ ] **Step 2: Import-check**

Run: `cd python && uv run python -c "from chunkshop.extractors import ExtractResult, Extractor, load_extractor; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add python/src/chunkshop/extractors/__init__.py
git commit -m "chore(extractors): re-export ExtractResult from package"
```

---

## Task 7: Update `runner.py` to merge extracted metadata into chunks

**Files:**
- Modify: `python/src/chunkshop/runner.py`

The extractor previously produced `list[str]` which went straight to `sink.write_document(..., tags)`. Now it returns `ExtractResult` — we need the tags for the sink call (unchanged signature) and we need to merge `result.metadata` into each `Chunk.metadata` before writing. `Chunk` is frozen, so we'll replace each chunk with a new one via `dataclasses.replace`.

- [ ] **Step 1: Locate the existing extractor call in `runner.run_cell`**

In `python/src/chunkshop/runner.py`, find this block (currently near the bottom of the `for doc in source.iter_documents():` loop):

```python
embeddings = embedder.embed(texts)
tags = [extractor.extract(c.original_content) for c in chunks]
sink.write_document(doc.id, chunks, embeddings, tags)
```

- [ ] **Step 2: Replace with the new flow**

```python
embeddings = embedder.embed(texts)
from dataclasses import replace as _replace
results = [extractor.extract(c.original_content) for c in chunks]
tags = [r.tags for r in results]
chunks = [
    _replace(c, metadata={**c.metadata, **r.metadata})
    for c, r in zip(chunks, results)
]
sink.write_document(doc.id, chunks, embeddings, tags)
```

Move the `from dataclasses import replace as _replace` to the top of the file alongside other imports if not already present.

- [ ] **Step 3: Run full suite — should pass (no integration test for metadata merge yet, but existing tests unbroken)**

Run: `cd python && uv run pytest -q`
Expected: same pass count as Task 1 Step 2.

- [ ] **Step 4: Commit**

```bash
git add python/src/chunkshop/runner.py
git commit -m "feat(runner): merge extractor metadata into chunk metadata"
```

---

## ⛔ DC-003 Drift Check: Extractor contract migration

**Re-read:** `skill-output/mission-brief/Mission-Brief-schema-flexibility.md`. Verify SC-005 (extractor contract) and SC-008 (existing configs unchanged).

**Gate:**
- [ ] `cd python && uv run pytest -q` — same pass count as baseline.
- [ ] `cd python && uv run python -c "from chunkshop.extractors import ExtractResult, load_extractor; from chunkshop.config import NoneExtractor; r = load_extractor(NoneExtractor()).extract('x'); assert hasattr(r, 'tags') and hasattr(r, 'metadata'); print('contract ok')"`
- [ ] If anything fails, stop and investigate before proceeding.

---

## Task 8: Add `PromoteColumn` pydantic model

**Files:**
- Modify: `python/src/chunkshop/config.py`

- [ ] **Step 1: Write the test first**

File `python/tests/chunkshop/test_config_target_flexibility.py` (NEW):

```python
import pytest
from pydantic import ValidationError

from chunkshop.config import PromoteColumn, TargetConfig


def test_promote_column_valid():
    pc = PromoteColumn(path="language", type="text")
    assert pc.path == "language"
    assert pc.type == "text"


def test_promote_column_dotted_path():
    pc = PromoteColumn(path="entities.ORG", type="text[]")
    assert pc.path == "entities.ORG"


def test_promote_column_rejects_bad_ident():
    with pytest.raises(ValidationError):
        PromoteColumn(path="DROP TABLE", type="text")


def test_promote_column_rejects_bad_type():
    with pytest.raises(ValidationError):
        PromoteColumn(path="language", type="blob;DROP TABLE users")
```

- [ ] **Step 2: Run — expect failure (import error)**

Run: `cd python && uv run pytest tests/chunkshop/test_config_target_flexibility.py -v`
Expected: FAIL — `ImportError: cannot import name 'PromoteColumn'`.

- [ ] **Step 3: Add `PromoteColumn` to `config.py`**

In `python/src/chunkshop/config.py`, find the `TargetConfig` definition and immediately before it add:

```python
_ALLOWED_PROMOTE_TYPES = {"text", "text[]", "int", "bigint", "boolean", "jsonb", "timestamptz", "date"}
_PATH_SEGMENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class PromoteColumn(_Base):
    path: str
    type: str

    @field_validator("path")
    @classmethod
    def _safe_path(cls, v: str) -> str:
        if not v or not all(_PATH_SEGMENT.match(seg) for seg in v.split(".")):
            raise ValueError(
                f"path segments must match ^[a-z_][a-z0-9_]*$ separated by '.', got {v!r}"
            )
        return v

    @field_validator("type")
    @classmethod
    def _safe_type(cls, v: str) -> str:
        if v not in _ALLOWED_PROMOTE_TYPES:
            raise ValueError(
                f"promote_metadata type must be one of {_ALLOWED_PROMOTE_TYPES}, got {v!r}"
            )
        return v
```

`_Base`, `re`, and `field_validator` are already imported at the top of `config.py`.

- [ ] **Step 4: Re-run — expect pass**

Run: `cd python && uv run pytest tests/chunkshop/test_config_target_flexibility.py -v`
Expected: all four PromoteColumn tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_config_target_flexibility.py
git commit -m "feat(config): add PromoteColumn model with ident/type safelists"
```

---

## Task 9: Extend `TargetConfig` with `mode`, `source_tag`, `promote_metadata`, `force_overwrite`

**Files:**
- Modify: `python/src/chunkshop/config.py`
- Modify: `python/tests/chunkshop/test_config_target_flexibility.py`

- [ ] **Step 1: Append tests to `test_config_target_flexibility.py`**

```python
def test_target_default_mode_is_overwrite():
    cfg = TargetConfig(dsn_env="X", **{"schema": "s"}, table="t")
    assert cfg.mode == "overwrite"
    assert cfg.source_tag is None
    assert cfg.promote_metadata == []
    assert cfg.force_overwrite is False


def test_target_append_requires_source_tag():
    with pytest.raises(ValidationError, match="source_tag"):
        TargetConfig(dsn_env="X", **{"schema": "s"}, table="t", mode="append")


def test_target_append_with_source_tag_ok():
    cfg = TargetConfig(
        dsn_env="X", **{"schema": "s"}, table="t",
        mode="append", source_tag="pdfs_q2_2026",
    )
    assert cfg.mode == "append"
    assert cfg.source_tag == "pdfs_q2_2026"


def test_target_source_tag_ident_safe():
    with pytest.raises(ValidationError):
        TargetConfig(
            dsn_env="X", **{"schema": "s"}, table="t",
            mode="append", source_tag="bad; drop table",
        )


def test_target_promote_metadata_parses():
    cfg = TargetConfig(
        dsn_env="X", **{"schema": "s"}, table="t",
        promote_metadata=[
            {"path": "language", "type": "text"},
            {"path": "entities.ORG", "type": "text[]"},
        ],
    )
    assert len(cfg.promote_metadata) == 2
    assert cfg.promote_metadata[0].path == "language"
    assert cfg.promote_metadata[1].type == "text[]"
```

- [ ] **Step 2: Run — expect failure**

Run: `cd python && uv run pytest tests/chunkshop/test_config_target_flexibility.py -v`
Expected: five new tests FAIL.

- [ ] **Step 3: Extend `TargetConfig` in `config.py`**

Replace the existing `TargetConfig` class:

```python
from pydantic import model_validator  # add to the existing pydantic imports at top


class TargetConfig(_Base):
    dsn_env: str = "AGE_BAKEOFF_PGRG_DSN"
    schema_name: str = Field(alias="schema")
    table: str
    overwrite: bool = False  # legacy; see `mode` for the new path
    hnsw: bool = True
    mode: Literal["overwrite", "append", "create_if_missing"] = "overwrite"
    source_tag: Optional[str] = None
    promote_metadata: list[PromoteColumn] = Field(default_factory=list)
    force_overwrite: bool = False

    @field_validator("table", "schema_name", "source_tag")
    @classmethod
    def _safe_ident(cls, v):
        if v is None:
            return v
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(f"table/schema/source_tag must match ^[a-z_][a-z0-9_]*$, got {v!r}")
        return v

    @model_validator(mode="after")
    def _append_requires_source_tag(self):
        if self.mode == "append" and not self.source_tag:
            raise ValueError("source_tag is required when mode='append'")
        return self
```

The `_safe_ident` validator now accepts `None` (for `source_tag`) and covers all three identifier fields.

- [ ] **Step 4: Re-run — expect pass**

Run: `cd python && uv run pytest tests/chunkshop/test_config_target_flexibility.py -v`
Expected: all TargetConfig tests PASS; PromoteColumn tests still pass.

- [ ] **Step 5: Full-suite regression**

Run: `cd python && uv run pytest -q`
Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_config_target_flexibility.py
git commit -m "feat(config): TargetConfig gains mode, source_tag, promote_metadata, force_overwrite"
```

---

## ⛔ DC-001 Drift Check: pydantic additions

**Re-read:** `skill-output/mission-brief/Mission-Brief-schema-flexibility.md`. Verify SC-001, SC-004.

**Gate:**
- [ ] `cd python && uv run pytest tests/chunkshop/test_config_target_flexibility.py -v` — all 9 tests pass.
- [ ] `cd python && uv run pytest -q` — no regressions.
- [ ] Stop and fix before moving to Sink work if anything fails.

---

## Task 10: Sink pre-flight for append mode — table-missing and dim-mismatch failures

**Files:**
- Modify: `python/src/chunkshop/sink.py`
- Create: `python/tests/chunkshop/test_sink_append_mode.py`

We start with the failing cases per DC-002 ("verify SC-002 with a failing-case integration test before doing the happy-path").

- [ ] **Step 1: Write failing-case tests**

File `python/tests/chunkshop/test_sink_append_mode.py` (NEW):

```python
import os
import pytest
import psycopg

from chunkshop.config import TargetConfig
from chunkshop.sink import PgVectorSink


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
    # Cleanup
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_append CASCADE")
        conn.commit()


def _mk_target(**overrides) -> TargetConfig:
    kwargs = {
        "dsn_env": DSN_ENV,
        "schema": "chunkshop_test_append",
        "table": "target_a",
        "hnsw": False,
    }
    kwargs.update(overrides)
    return TargetConfig(**kwargs)


def test_append_fails_when_table_missing(ensure_pg):
    cfg = _mk_target(mode="append", source_tag="pdfs")
    sink = PgVectorSink(cfg, embed_dim=4)
    with pytest.raises(RuntimeError, match="does not exist"):
        sink.create_table()


def test_append_fails_on_dim_mismatch(ensure_pg):
    # Create the table with dim=4
    cfg_create = _mk_target(mode="create_if_missing", source_tag="pdfs")
    PgVectorSink(cfg_create, embed_dim=4).create_table()

    # Try to append with dim=8 — should fail pre-flight
    cfg_bad = _mk_target(mode="append", source_tag="pdfs")
    with pytest.raises(RuntimeError, match="dim"):
        PgVectorSink(cfg_bad, embed_dim=8).create_table()
```

- [ ] **Step 2: Run — expect failure (mode is ignored by current sink)**

Run: `cd python && uv run pytest tests/chunkshop/test_sink_append_mode.py -v`
Expected: FAILs (either "no such thing as RuntimeError" or wrong error type — proves preflight doesn't exist).

- [ ] **Step 3: Refactor `PgVectorSink.create_table` with mode awareness**

Open `python/src/chunkshop/sink.py`. Replace `create_table` with this decomposed version (keep the existing imports and the `_fq` helper):

```python
def create_table(self) -> None:
    """Ensure target schema + table per `cfg.mode`.

    Modes:
      - overwrite: DROP TABLE IF EXISTS (safety-checked below), then CREATE.
      - append:    require table to exist; pre-flight (dim, source col, promoted cols).
      - create_if_missing: CREATE IF NOT EXISTS; no pre-flight.
    """
    with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS vector"))
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
            sql.Identifier(self.cfg.schema_name)
        ))

        if self.cfg.mode == "overwrite":
            self._overwrite_create(cur)
        elif self.cfg.mode == "append":
            self._append_preflight(cur)
        elif self.cfg.mode == "create_if_missing":
            self._create_if_missing(cur)
        else:
            raise ValueError(f"unknown mode: {self.cfg.mode}")

        conn.commit()

def _table_exists(self, cur) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=%s AND tablename=%s)",
        (self.cfg.schema_name, self.cfg.table),
    )
    return cur.fetchone()[0]

def _current_embed_dim(self, cur) -> int | None:
    """Returns the existing embedding vector dim, or None if column missing."""
    cur.execute(
        """
        SELECT atttypmod
        FROM pg_attribute
        WHERE attrelid = (
            SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s AND n.nspname = %s
        )
        AND attname = 'embedding'
        """,
        (self.cfg.table, self.cfg.schema_name),
    )
    row = cur.fetchone()
    if row is None:
        return None
    # For pgvector: atttypmod is the declared dim. pgvector sometimes returns raw dim,
    # sometimes dim with a header — most reliable is `SELECT vector_dims(embedding) FROM t LIMIT 1`
    cur.execute(
        sql.SQL("SELECT vector_dims(embedding) FROM {} LIMIT 1").format(self._fq())
    )
    r = cur.fetchone()
    return r[0] if r else None

def _create_base_ddl(self, cur) -> None:
    fq = self._fq()
    cur.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {tbl} (
            id text PRIMARY KEY,
            doc_id text NOT NULL,
            seq_num int NOT NULL,
            original_content text NOT NULL,
            embedded_content text NOT NULL,
            tags text[] NOT NULL DEFAULT '{{}}',
            metadata jsonb NOT NULL DEFAULT '{{}}',
            embedding vector({dim}) NOT NULL,
            source text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """).format(tbl=fq, dim=sql.Literal(self.embed_dim)))
    cur.execute(sql.SQL(
        "CREATE INDEX IF NOT EXISTS {name} ON {tbl} (doc_id, seq_num)"
    ).format(name=sql.Identifier(f"{self.cfg.table}_doc_seq_idx"), tbl=fq))
    if self.cfg.hnsw:
        cur.execute(sql.SQL(
            "CREATE INDEX IF NOT EXISTS {name} ON {tbl} "
            "USING hnsw (embedding vector_cosine_ops)"
        ).format(name=sql.Identifier(f"{self.cfg.table}_emb_hnsw_idx"), tbl=fq))
    self._ensure_promote_columns(cur)

def _ensure_promote_columns(self, cur) -> None:
    fq = self._fq()
    for pc in self.cfg.promote_metadata:
        col_ident = sql.Identifier(pc.path.replace(".", "__"))
        cur.execute(
            sql.SQL("ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} " + pc.type).format(
                tbl=fq, col=col_ident
            )
        )

def _overwrite_create(self, cur) -> None:
    # Safety check lives in Task 12 — for now, drop + recreate (current behavior).
    if self._table_exists(cur):
        cur.execute(sql.SQL("DROP TABLE {}").format(self._fq()))
    self._create_base_ddl(cur)

def _create_if_missing(self, cur) -> None:
    if not self._table_exists(cur):
        self._create_base_ddl(cur)
    else:
        # Table exists — ensure source column + promoted columns present.
        cur.execute(
            sql.SQL("ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS source text").format(tbl=self._fq())
        )
        self._ensure_promote_columns(cur)

def _append_preflight(self, cur) -> None:
    if not self._table_exists(cur):
        raise RuntimeError(
            f"append mode: table {self.cfg.schema_name}.{self.cfg.table} does not exist. "
            f"Use mode='create_if_missing' on the first cell."
        )
    current_dim = self._current_embed_dim(cur)
    if current_dim is not None and current_dim != self.embed_dim:
        raise RuntimeError(
            f"append mode: target embedding dim is {current_dim}, cell's embedder dim is "
            f"{self.embed_dim}. Vectors are not comparable."
        )
    # Ensure source column + promoted columns present.
    cur.execute(
        sql.SQL("ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS source text").format(tbl=self._fq())
    )
    self._ensure_promote_columns(cur)
```

Delete the old `create_table` body entirely; keep `__init__`, `_fq`, `write_document`, `count_docs`.

- [ ] **Step 4: Re-run tests — expect pass**

Run: `cd python && uv run pytest tests/chunkshop/test_sink_append_mode.py -v`
Expected: both failing-case tests PASS.

- [ ] **Step 5: Full suite (existing `test_sink.py` still uses `mode=overwrite` default)**

Run: `cd python && uv run pytest -q`
Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add python/src/chunkshop/sink.py python/tests/chunkshop/test_sink_append_mode.py
git commit -m "feat(sink): mode-aware create_table + append preflight (SC-002 failure cases)"
```

---

## Task 11: Sink append happy-path and promote_metadata column creation

**Files:**
- Modify: `python/tests/chunkshop/test_sink_append_mode.py`

- [ ] **Step 1: Add happy-path tests**

Append to `test_sink_append_mode.py`:

```python
def test_append_preflight_adds_missing_source_column(ensure_pg):
    # Create the table in overwrite mode (no source column yet — simulating pre-v0.3.0 table).
    cfg_old = _mk_target(mode="overwrite")
    PgVectorSink(cfg_old, embed_dim=4).create_table()

    # Manually drop the source column to simulate a pre-existing table missing it
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE chunkshop_test_append.target_a DROP COLUMN IF EXISTS source")
        conn.commit()

    # Now append — should auto-add `source` column.
    cfg_append = _mk_target(mode="append", source_tag="pdfs")
    PgVectorSink(cfg_append, embed_dim=4).create_table()

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s",
            ("chunkshop_test_append", "target_a"),
        )
        cols = {r[0] for r in cur.fetchall()}
        assert "source" in cols


def test_append_adds_promote_columns(ensure_pg):
    cfg_create = _mk_target(mode="create_if_missing", source_tag="pdfs")
    PgVectorSink(cfg_create, embed_dim=4).create_table()

    cfg_append = _mk_target(
        mode="append",
        source_tag="pdfs",
        promote_metadata=[
            {"path": "language", "type": "text"},
            {"path": "entities.ORG", "type": "text[]"},
        ],
    )
    PgVectorSink(cfg_append, embed_dim=4).create_table()

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s",
            ("chunkshop_test_append", "target_a"),
        )
        cols = {r[0] for r in cur.fetchall()}
        # Dotted paths become double-underscored to stay a single ident.
        assert "language" in cols
        assert "entities__org" in cols
```

- [ ] **Step 2: Run — expect pass (the preflight + promote logic is already in place from Task 10)**

Run: `cd python && uv run pytest tests/chunkshop/test_sink_append_mode.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add python/tests/chunkshop/test_sink_append_mode.py
git commit -m "test(sink): happy-path append preflight adds source + promoted columns"
```

---

## ⛔ DC-002 Drift Check: Sink pre-flight

**Re-read:** `skill-output/mission-brief/Mission-Brief-schema-flexibility.md`. Verify SC-002.

**Gate:**
- [ ] `cd python && uv run pytest tests/chunkshop/test_sink_append_mode.py -v` — 4 tests pass.
- [ ] `cd python && uv run pytest -q` — no regressions.
- [ ] Confirm in `sink.py`: `_append_preflight` runs dim check BEFORE any schema mutations. Mutation-before-check is a bug.

---

## Task 12: Sink overwrite-safety — refuse foreign source_tag without `force_overwrite`

**Files:**
- Modify: `python/src/chunkshop/sink.py`
- Modify: `python/tests/chunkshop/test_sink_append_mode.py`

- [ ] **Step 1: Write tests**

Append to `test_sink_append_mode.py`:

```python
def test_overwrite_refuses_foreign_source_tag(ensure_pg):
    # First cell populates the table with source_tag=pdfs
    cfg_a = _mk_target(mode="create_if_missing", source_tag="pdfs")
    sink_a = PgVectorSink(cfg_a, embed_dim=4)
    sink_a.create_table()
    # Simulate one row with source='pdfs'
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chunkshop_test_append.target_a "
            "(id, doc_id, seq_num, original_content, embedded_content, "
            " tags, metadata, embedding, source) "
            "VALUES ('d1::0','d1',0,'x','x','{}','{}'::jsonb, '[1,0,0,0]'::vector, 'pdfs')"
        )
        conn.commit()

    # Second cell in overwrite mode with a different source_tag — should refuse
    cfg_b = _mk_target(mode="overwrite", source_tag="web_scrape")
    with pytest.raises(RuntimeError, match="source_tag"):
        PgVectorSink(cfg_b, embed_dim=4).create_table()


def test_overwrite_force_bypasses_check(ensure_pg):
    cfg_a = _mk_target(mode="create_if_missing", source_tag="pdfs")
    PgVectorSink(cfg_a, embed_dim=4).create_table()
    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chunkshop_test_append.target_a "
            "(id, doc_id, seq_num, original_content, embedded_content, "
            " tags, metadata, embedding, source) "
            "VALUES ('d1::0','d1',0,'x','x','{}','{}'::jsonb, '[1,0,0,0]'::vector, 'pdfs')"
        )
        conn.commit()

    cfg_force = _mk_target(mode="overwrite", source_tag="web_scrape", force_overwrite=True)
    # Should not raise
    PgVectorSink(cfg_force, embed_dim=4).create_table()
```

- [ ] **Step 2: Run — expect failure**

Run: `cd python && uv run pytest tests/chunkshop/test_sink_append_mode.py::test_overwrite_refuses_foreign_source_tag -v`
Expected: FAIL.

- [ ] **Step 3: Add safety check to `_overwrite_create`**

Replace the `_overwrite_create` method in `sink.py`:

```python
def _overwrite_create(self, cur) -> None:
    if self._table_exists(cur) and not self.cfg.force_overwrite:
        # Check for existing rows with a different source_tag.
        cur.execute(
            sql.SQL("SELECT DISTINCT source FROM {tbl} WHERE source IS NOT NULL LIMIT 10").format(
                tbl=self._fq()
            )
        )
        existing_tags = {r[0] for r in cur.fetchall()}
        my_tag = self.cfg.source_tag
        foreign = existing_tags - ({my_tag} if my_tag else set())
        if foreign:
            raise RuntimeError(
                f"overwrite refuses to drop {self.cfg.schema_name}.{self.cfg.table}: "
                f"table holds rows with source_tag values {sorted(foreign)!r} that differ "
                f"from this cell's source_tag {my_tag!r}. Set target.force_overwrite: true "
                f"to bypass."
            )
    if self._table_exists(cur):
        cur.execute(sql.SQL("DROP TABLE {}").format(self._fq()))
    self._create_base_ddl(cur)
```

- [ ] **Step 4: Re-run tests — expect pass**

Run: `cd python && uv run pytest tests/chunkshop/test_sink_append_mode.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sink.py python/tests/chunkshop/test_sink_append_mode.py
git commit -m "feat(sink): overwrite refuses foreign source_tag without force_overwrite"
```

---

## Task 13: Sink `write_document` populates source + promoted columns

**Files:**
- Modify: `python/src/chunkshop/sink.py`
- Modify: `python/tests/chunkshop/test_sink_append_mode.py`

- [ ] **Step 1: Add test**

Append to `test_sink_append_mode.py`:

```python
import numpy as np
from chunkshop.chunkers.base import Chunk


def test_write_populates_source_and_promoted(ensure_pg):
    cfg = _mk_target(
        mode="create_if_missing",
        source_tag="pdfs",
        promote_metadata=[
            {"path": "language", "type": "text"},
            {"path": "entities.ORG", "type": "text[]"},
        ],
    )
    sink = PgVectorSink(cfg, embed_dim=4)
    sink.create_table()
    chunks = [
        Chunk(
            doc_id="d1", seq_num=0, original_content="x", embedded_content="x",
            metadata={"language": "en", "entities": {"ORG": ["Acme", "Northwind"]}},
        ),
    ]
    embeddings = np.array([[1, 0, 0, 0]], dtype=np.float32)
    tags = [["t"]]
    sink.write_document("d1", chunks, embeddings, tags)

    with psycopg.connect(os.environ[DSN_ENV]) as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT source, language, "entities__org" FROM chunkshop_test_append.target_a'
        )
        row = cur.fetchone()
        assert row[0] == "pdfs"
        assert row[1] == "en"
        assert row[2] == ["Acme", "Northwind"]
```

- [ ] **Step 2: Run — expect failure**

Run: `cd python && uv run pytest tests/chunkshop/test_sink_append_mode.py::test_write_populates_source_and_promoted -v`
Expected: FAIL — insert doesn't touch source or promoted columns yet.

- [ ] **Step 3: Update `write_document`**

Replace the `write_document` method in `sink.py`:

```python
def write_document(
    self,
    doc_id: str,
    chunks: list[Chunk],
    embeddings: np.ndarray,
    tags_per_chunk: list[list[str]],
) -> None:
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) length mismatch"
        )
    if len(chunks) != len(tags_per_chunk):
        raise ValueError(
            f"chunks ({len(chunks)}) and tags ({len(tags_per_chunk)}) length mismatch"
        )
    fq = self._fq()
    promote = self.cfg.promote_metadata
    promote_cols = [sql.Identifier(pc.path.replace(".", "__")) for pc in promote]
    base_cols = [
        sql.Identifier(c) for c in
        ("id", "doc_id", "seq_num", "original_content", "embedded_content",
         "tags", "metadata", "embedding", "source")
    ]
    all_cols = base_cols + promote_cols
    placeholders = sql.SQL(", ").join(
        [sql.SQL("%s")] * 5 + [sql.SQL("%s"), sql.SQL("%s::jsonb"), sql.SQL("%s::vector"), sql.SQL("%s")]
        + [sql.SQL("%s")] * len(promote)
    )
    stmt = sql.SQL(
        "INSERT INTO {tbl} ({cols}) VALUES ({vals}) "
        "ON CONFLICT (id) DO UPDATE SET "
        "original_content = EXCLUDED.original_content, "
        "embedded_content = EXCLUDED.embedded_content, "
        "tags = EXCLUDED.tags, metadata = EXCLUDED.metadata, "
        "embedding = EXCLUDED.embedding, source = EXCLUDED.source"
        + (", " + ", ".join(
            f'"{pc.path.replace(".", "__")}" = EXCLUDED."{pc.path.replace(".", "__")}"'
            for pc in promote
        ) if promote else "")
    ).format(
        tbl=fq,
        cols=sql.SQL(", ").join(all_cols),
        vals=placeholders,
    )

    rows = []
    for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
        vec_literal = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
        base = [
            f"{c.doc_id}::{c.seq_num}",
            c.doc_id,
            c.seq_num,
            c.original_content,
            c.embedded_content,
            tags,
            json.dumps(c.metadata),
            vec_literal,
            self.cfg.source_tag,
        ]
        promoted_values = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
        rows.append(tuple(base + promoted_values))

    with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
        cur.executemany(stmt, rows)
        conn.commit()
```

Add this module-level helper at the top of `sink.py` (after imports):

```python
def _jsonb_path_get(meta: dict, path: str):
    """Traverse a dotted path through nested dicts. Returns None if any segment missing."""
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur
```

- [ ] **Step 4: Re-run — expect pass**

Run: `cd python && uv run pytest tests/chunkshop/test_sink_append_mode.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Full-suite sanity**

Run: `cd python && uv run pytest -q`
Expected: all tests pass (`test_sink.py` should still pass because `overwrite` is still the default mode with no `source_tag` — `source` column is NULL for that flow).

- [ ] **Step 6: Commit**

```bash
git add python/src/chunkshop/sink.py python/tests/chunkshop/test_sink_append_mode.py
git commit -m "feat(sink): write populates source + promoted columns from chunk metadata"
```

---

## Task 14: Multi-source end-to-end integration test

**Files:**
- Create: `python/tests/chunkshop/test_multi_source_ingest.py`

- [ ] **Step 1: Write the integration test**

File `python/tests/chunkshop/test_multi_source_ingest.py` (NEW):

```python
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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_multi CASCADE")
        conn.commit()


def _json_corpus_fixture(tmp_path, prefix: str):
    import json
    path = tmp_path / f"{prefix}.json"
    path.write_text(json.dumps({
        "documents": [
            {"id": f"{prefix}_1", "title": "t1", "content": "# Alpha\n\nAlpha bravo charlie."},
            {"id": f"{prefix}_2", "title": "t2", "content": "# Delta\n\nDelta echo foxtrot."},
        ]
    }))
    return str(path)


def test_two_cells_append_into_one_table(ensure_pg, tmp_path):
    dsn = ensure_pg
    corpus_a = _json_corpus_fixture(tmp_path, "cell_a")
    corpus_b = _json_corpus_fixture(tmp_path, "cell_b")

    common_target = {
        "dsn_env": DSN_ENV,
        "schema": "chunkshop_test_multi",
        "table": "unified",
        "hnsw": False,
    }

    cfg_a = CellConfig(
        cell_name="cell_a",
        source={"type": "json_corpus", "path": corpus_a},
        chunker={"type": "hierarchy"},
        embedder={
            "type": "fastembed",
            "model_name": "Xenova/bge-small-en-v1.5-int8",
            "dim": 384,
            "threads": 2,
        },
        target={**common_target, "mode": "create_if_missing", "source_tag": "cell_a_source"},
    )
    cfg_b = CellConfig(
        cell_name="cell_b",
        source={"type": "json_corpus", "path": corpus_b},
        chunker={"type": "hierarchy"},
        embedder={
            "type": "fastembed",
            "model_name": "Xenova/bge-small-en-v1.5-int8",
            "dim": 384,
            "threads": 2,
        },
        target={**common_target, "mode": "append", "source_tag": "cell_b_source"},
    )

    r1 = run_cell(cfg_a)
    assert r1.error is None, r1.error
    r2 = run_cell(cfg_b)
    assert r2.error is None, r2.error

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT source, COUNT(*) FROM chunkshop_test_multi.unified GROUP BY source")
        by_source = dict(cur.fetchall())
        assert by_source.get("cell_a_source", 0) > 0
        assert by_source.get("cell_b_source", 0) > 0

        cur.execute("SELECT COUNT(*) FROM chunkshop_test_multi.unified WHERE source='cell_a_source'")
        only_a = cur.fetchone()[0]
        assert only_a == by_source["cell_a_source"]
```

- [ ] **Step 2: Run the test — expect pass**

Run: `cd python && uv run pytest tests/chunkshop/test_multi_source_ingest.py -v`
Expected: PASS. (This requires the fastembed model cache — if the int8 model isn't cached yet, this test's first run downloads ~35 MB.)

- [ ] **Step 3: Commit**

```bash
git add python/tests/chunkshop/test_multi_source_ingest.py
git commit -m "test(integration): two cells append into one table with different source_tag (SC-006)"
```

---

## Task 15: Regression — existing cell config still runs unchanged

**Files:** (no code changes — verification only)

- [ ] **Step 1: Run the stock example**

Run: `cd python && uv run chunkshop ingest --config src/chunkshop/configs/example-files-to-bge.yaml --doc-limit 0`

Expected: exit code 0; `docs_processed: 0` and `chunks_written: 0` in the JSON summary. Because `doc_limit=0` means the loop never iterates, but the pre-flight (create_table) still runs — this exercises the default `mode=overwrite` path end-to-end. If it errors on pydantic validation or unknown fields, the default-value changes broke backward compat.

Note: this requires `CHUNKSHOP_DSN` to point at a working Postgres, or the sink construction fails. If you don't have a spare DB, skip this step and rely on `test_sink.py`'s regression coverage instead.

- [ ] **Step 2: Verify the sample configs in `docs/samples/` still parse**

Run:

```bash
cd python && uv run python -c "
from chunkshop.config import load_config
for p in ['../docs/samples/sample.yaml', '../docs/samples/sample-sentence-aware.yaml', '../docs/samples/sample-neighbor-expand.yaml']:
    cfg = load_config(p)
    print(f'OK  {p}  mode={cfg.target.mode}')
"
```

Expected: three `OK ...` lines, all with `mode=overwrite` (the default).

- [ ] **Step 3: Commit — no files changed, but tag the verification in a note**

Skip the commit; no files changed. Note SC-008 satisfied.

---

## ⛔ DC-004 Drift Check: Pre-tutorial gate

**Re-read:** `skill-output/mission-brief/Mission-Brief-schema-flexibility.md`. Verify every SC-001…SC-008 has evidence in code and/or passing tests. Verify nothing in the Out of Scope list has been built.

**Gate:**
- [ ] `cd python && uv run pytest -q` — all tests pass, including the new DB integration tests if DB reachable.
- [ ] Docs you're about to write (SC-009, SC-010) describe only features that exist in code. Re-read the commits for Tasks 7-14 and list the user-facing capabilities.
- [ ] If any SC is under-covered, write the missing test BEFORE authoring tutorial prose around misleading capabilities.

---

## Task 16: Write `docs/tutorial-multi-source.md`

**Files:**
- Create: `docs/tutorial-multi-source.md`

- [ ] **Step 1: Write the tutorial**

File `docs/tutorial-multi-source.md` (NEW):

````markdown
# Tutorial: unify multiple sources in one retrieval table

This tutorial walks through ingesting two different sources — markdown files (representing output from `yonk-doctools` PDF prep) and a JSON corpus (representing API-exported support tickets) — into a single pgvector table with a `source` discriminator column and a promoted `language` column.

End state: one table `mydata.all_docs` containing rows from both sources, filterable by `source`, with `language` indexable as a first-class column.

## Prereqs

- chunkshop v0.3.0+ (schema-flexibility features).
- A Postgres with pgvector (see [`tutorial.md`](tutorial.md) for `docker run`).
- `export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"`.
- Optional: the `[lang]` extra (`uv sync --extra lang`) to populate the `language` metadata. Without it, the promoted column will be NULL; the tutorial still works.

## Step 1 — Cell A: ingest markdown files, create the unified table

```yaml
# cell-a-markdown.yaml
cell_name: docs_markdown
source:
  type: files
  glob: docs/samples/*.md
  id_from: stem
chunker:
  type: hierarchy
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: lang_detect          # optional — populates metadata.language if [lang] extra installed
  backend: langdetect
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: all_docs
  mode: create_if_missing    # first cell creates the table if not present
  source_tag: docs_markdown
  promote_metadata:
    - path: language
      type: text
```

Run it:

```bash
chunkshop ingest --config cell-a-markdown.yaml
```

Verify:

```bash
psql "$CHUNKSHOP_DSN" -c "SELECT COUNT(*), COUNT(DISTINCT source) FROM mydata.all_docs"
```

You should see a count matching the sample corpus (~8 chunks) and `COUNT(DISTINCT source) = 1` with value `docs_markdown`.

## Step 2 — Cell B: ingest a JSON corpus, append to the same table

Fabricate a small JSON corpus at `tickets.json`:

```json
{"documents": [
  {"id": "t1", "title": "Login", "content": "# Login issues\n\nUsers report intermittent login failures."},
  {"id": "t2", "title": "Export", "content": "# Export failing\n\nCSV export times out on large datasets."}
]}
```

Cell B config:

```yaml
# cell-b-tickets.yaml
cell_name: support_tickets
source:
  type: json_corpus
  path: ./tickets.json
chunker:
  type: hierarchy
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: lang_detect
  backend: langdetect
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: all_docs
  mode: append               # second cell appends — pre-flight verifies dim match
  source_tag: support_tickets
  promote_metadata:
    - path: language
      type: text
```

Run:

```bash
chunkshop ingest --config cell-b-tickets.yaml
```

Note: pre-flight checks that the target table exists and that the embedding dim (384) matches. Cell B and Cell A share the same embedder, so the check passes.

## Step 3 — Verify the unification

```sql
-- Total rows across sources
SELECT source, COUNT(*) FROM mydata.all_docs GROUP BY source;
--       source       | count
-- -------------------+-------
--  docs_markdown     |     8
--  support_tickets   |     2

-- Language is a promoted column — you can filter / GROUP BY it
SELECT source, language, COUNT(*) FROM mydata.all_docs GROUP BY source, language;

-- Ingest times from the orchestrator output will differ per cell;
-- record them before moving on so you can set SLAs on future runs.
```

## Step 4 — A cross-source retrieval query

```python
# query.py — same as docs/tutorial.md Step 6, but filtered by source
import os, psycopg
from fastembed import TextEmbedding
import chunkshop.embedders  # register int8 variant

qvec = list(TextEmbedding(model_name="Xenova/bge-small-en-v1.5-int8").embed(["why are logins failing"]))[0]
qlit = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

with psycopg.connect(os.environ["CHUNKSHOP_DSN"]) as conn, conn.cursor() as cur:
    # Search everything
    cur.execute(
        """
        SELECT source, doc_id, seq_num, original_content,
               embedding <=> %s::vector AS distance
        FROM mydata.all_docs
        ORDER BY embedding <=> %s::vector
        LIMIT 3
        """, (qlit, qlit),
    )
    for row in cur.fetchall():
        print(row[:4], f"dist={row[4]:.4f}")

    # Restrict to tickets only
    cur.execute(
        """
        SELECT source, doc_id, seq_num
        FROM mydata.all_docs
        WHERE source = 'support_tickets'
        ORDER BY embedding <=> %s::vector LIMIT 3
        """, (qlit,),
    )
    print("---filtered---")
    for row in cur.fetchall():
        print(row)
```

The unfiltered query should return the login-issues chunk as top-1. The filtered query returns only ticket rows regardless of score.

## Step 5 — Clean up or iterate

- Add another cell with `mode: append` and a new `source_tag` to layer a third source in.
- If you want to wipe a cell's rows only: `DELETE FROM mydata.all_docs WHERE source = 'docs_markdown'` — chunkshop does not provide this as a CLI operation on purpose (too destructive to bake in).
- To overwrite the entire table when it contains foreign-source rows, set `target.force_overwrite: true` in YAML — chunkshop refuses the implicit case.

## What this demonstrates

- **SC-001/003:** `mode: append` + `source_tag` populate the `source` column.
- **SC-002:** pre-flight verifies dim match and auto-adds missing `source`/promoted columns.
- **SC-004:** `promote_metadata` lifts `metadata.language` into a typed column.
- **SC-006:** two cells, one table, filter by source works.
- **SC-007:** switching Cell A to `mode: overwrite` without `force_overwrite` would fail after Cell B has loaded (try it to see the error).
````

- [ ] **Step 2: Render-check the markdown (no syntax errors)**

Run: `cd python && uv run python -c "import pathlib; md = pathlib.Path('../docs/tutorial-multi-source.md').read_text(); assert '<br' not in md; print('ok, len=', len(md))"`
Expected: `ok, len= <number>`.

- [ ] **Step 3: Commit**

```bash
git add docs/tutorial-multi-source.md
git commit -m "docs: tutorial for multi-source unified-table ingest (SC-009)"
```

---

## Task 17: Write `docs/quickstart-multi-source.md`

**Files:**
- Create: `docs/quickstart-multi-source.md`

- [ ] **Step 1: Write the quickstart**

File `docs/quickstart-multi-source.md` (NEW):

````markdown
# Quickstart: two sources → one table

Minimum YAML diff from the default `sample.yaml` to enable multi-source ingest.

## The change

```diff
 target:
   dsn_env: CHUNKSHOP_DSN
   schema: mydata
   table: all_docs
-  overwrite: true
+  mode: create_if_missing        # first cell; `append` for later cells
+  source_tag: pdfs_q2_2026       # required when mode=append
+  promote_metadata:              # optional — lifts jsonb paths to typed cols
+    - path: language
+      type: text
```

## Run two cells

```bash
export CHUNKSHOP_DSN="postgresql://postgres:postgres@localhost:5432/mydb"

# First cell creates the table:
chunkshop ingest --config cell-a.yaml      # mode: create_if_missing

# Second cell appends — pre-flight verifies dim match + schema compat:
chunkshop ingest --config cell-b.yaml      # mode: append
```

## Verify

```sql
\c mydb
SELECT source, COUNT(*) FROM mydata.all_docs GROUP BY source;
-- Two source_tag values, non-zero counts each.
\d mydata.all_docs
-- Columns include: source text, language text (if promoted), plus chunkshop defaults.
```

## Cheatsheet

| Want to…                                              | Set                                                                     |
|-------------------------------------------------------|-------------------------------------------------------------------------|
| Create the table                                      | `mode: create_if_missing`                                               |
| Add rows to an existing table                         | `mode: append` + `source_tag: <lowercase_ident>`                        |
| Drop and recreate (same cell as before)               | `mode: overwrite` (default when no `source_tag` conflict)               |
| Drop and recreate ignoring foreign source_tag rows    | `mode: overwrite` + `force_overwrite: true`                             |
| Promote a metadata path to a typed column             | `promote_metadata: [{path: entities.ORG, type: "text[]"}]`              |

Allowed `promote_metadata.type` values: `text`, `text[]`, `int`, `bigint`, `boolean`, `jsonb`, `timestamptz`, `date`.

Full walkthrough: [`tutorial-multi-source.md`](tutorial-multi-source.md).
````

- [ ] **Step 2: Commit**

```bash
git add docs/quickstart-multi-source.md
git commit -m "docs: quickstart for multi-source ingest (SC-010)"
```

---

## ⛔ DC-FINAL Drift Check

**Re-read:** `skill-output/mission-brief/Mission-Brief-schema-flexibility.md` one final time.

**SC coverage evidence:**

- [ ] **SC-001:** `TargetConfig.mode` literal validated in `test_config_target_flexibility.py::test_target_default_mode_is_overwrite`.
- [ ] **SC-002:** `test_sink_append_mode.py::test_append_fails_when_table_missing`, `test_append_fails_on_dim_mismatch`, `test_append_preflight_adds_missing_source_column`, `test_append_adds_promote_columns`.
- [ ] **SC-003:** `test_sink_append_mode.py::test_write_populates_source_and_promoted` + validator `_append_requires_source_tag` in `config.py`.
- [ ] **SC-004:** `test_config_target_flexibility.py::test_target_promote_metadata_parses` + `test_append_adds_promote_columns` + `test_write_populates_source_and_promoted`.
- [ ] **SC-005:** `test_extractor_rake.py` (updated) + `ExtractResult` dataclass + `runner.py` metadata merge.
- [ ] **SC-006:** `test_multi_source_ingest.py::test_two_cells_append_into_one_table`.
- [ ] **SC-007:** `test_sink_append_mode.py::test_overwrite_refuses_foreign_source_tag` + `test_overwrite_force_bypasses_check`.
- [ ] **SC-008:** Verified by `test_sink.py` (existing tests unchanged) + Task 15 step 1+2 verification.
- [ ] **SC-009:** `docs/tutorial-multi-source.md` exists and references the features demonstrated in tests.
- [ ] **SC-010:** `docs/quickstart-multi-source.md` exists.

**Regression verification:**
- [ ] `cd python && uv run pytest -q` — all tests pass (skip-on-no-Postgres is OK).
- [ ] `cd python && uv run chunkshop --version` — returns without error.
- [ ] Grep: `git grep -n "list\[str\]" python/src/chunkshop/extractors/` — should find NO occurrences referring to the old `extract()` return type. Any remaining need removal.

**Out-of-Scope check:**
- [ ] No code touches cross-DB support, schema migration for mismatched-dim tables, partitioning, RLS, or backward-compat extractor shims.

**Commit tag:**

```bash
git log --oneline main..HEAD
# Should show roughly 15 commits spanning:
#   feat(extractors): ExtractResult + Protocol + RakeKeywords + NoneExtractor
#   feat(runner): merge extractor metadata
#   feat(config): PromoteColumn + TargetConfig extensions
#   feat(sink): mode-aware create_table + append preflight + overwrite safety
#   feat(sink): write populates source + promoted columns
#   test(integration): two-cell multi-source
#   docs: tutorial + quickstart
```

Once every box in this checklist has evidence, this plan is complete. Recommend running chunkshop's existing `test_sink.py` one more time as a final backward-compat smoke before tagging v0.3.0.

---

## Notes for the executing agent

- **Worktree:** if you didn't already, create one before starting Task 2: `git worktree add ../chunkshop-schema-flex -b feat/schema-flexibility`. Prevents accidental mixing with other in-flight work.
- **Breaking change:** Task 3's Protocol change is a public-API break for anyone who wrote a custom extractor. Chunkshop is alpha; the mission brief authorizes this explicitly. If you discover an external consumer, stop and report it.
- **sink.py is growing.** It's now ~200 lines and has multiple responsibilities (schema management, write path, mode dispatch). Splitting it is tempting and **out of scope for this plan** — note the desire in a follow-up task but do not split here.
- **DB cleanup:** every DB integration test drops its schema in a teardown or in the test itself. If you see orphaned `chunkshop_test_*` schemas after a failed run, drop them manually — the tests will recreate on next run.

## Follow-ups (NOT this plan)

- Refactor `sink.py` into `sink/schema.py` + `sink/writer.py` + `sink/__init__.py`.
- Document `mode: create_if_missing` in the top-level `python/README.md` YAML reference table.
- Add a `chunkshop inspect --dsn ... --table ...` CLI that prints distinct `source` values and row counts (operational tool).
- Benchmark append-mode overhead vs overwrite-mode for realistic corpora.
