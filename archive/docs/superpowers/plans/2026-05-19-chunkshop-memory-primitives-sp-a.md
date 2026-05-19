# chunkshop Agent-Memory Primitives (SP-A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the write-side agent-memory primitives to chunkshop: a library staging API, a session-aware source, an episode framer, a user-wired consolidation chunker, and a memory sink that produces a two-tier (provisional/consolidated) pgvector store whose fact rows are 1:1 consumable by pg-raggraph.

**Architecture:** Everything is an ordinary chunkshop provider + pydantic config + loader branch, driven by `runner.run_cell` over two YAML presets (`memory/realtime.yaml`, `memory/consolidate.yaml`) sharing a chunkshop-owned append-only staging table. Consolidation "intelligence" is a user-wired callable mirroring the `build_summarizer` pattern; the v1 default is a zero-network extractive consolidator. The memory sink subclasses `PgSink` to add supersede (consolidated replaces provisional per session) and soft-invalidate (contradicted prior facts marked `retracted`).

**Tech Stack:** Python 3.12, pydantic v2 (`extra="forbid"` discriminated unions), psycopg 3, pgvector, fastembed (int8 bge-small, dim 384), pytest. SQLite (`:memory:`) for infra-free unit tests; Postgres via `$CHUNKSHOP_TEST_DSN` for integration (skip if unreachable).

**Spec:** `docs/superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md` (decisions D1–D9, success criteria SC-1…SC-7, invariants O1–O8).

**Plan-level conventions (apply to every task):**
- All paths are under `python/` (run `cd python` first). `uv run pytest` is the test runner.
- Integration tests use this exact fixture (copy verbatim where a task says "use the `ensure_pg` fixture"):

```python
import os, psycopg, pytest
DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"

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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_memory CASCADE")
        conn.commit()
```

- Transient metadata keys prefixed `_` (e.g. `_session_events`, `_episode_events`) are **never persisted**: the consolidation chunker pops them before emitting chunks; for the realtime path the cheap chunker ignores them and the memory sink strips any leftover `_`-prefixed keys before insert (Task 12).

---

### Task 1: Config — `SessionStagingSourceConfig`

**Files:**
- Modify: `python/src/chunkshop/config.py` (add model near `JsonCorpusSource` ~line 83; add to `SourceConfig` union ~line 166)
- Test: `python/tests/chunkshop/test_memory_config.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for memory-primitive pydantic config models."""
import pytest
from pydantic import ValidationError
from chunkshop.config import CellConfig


def _min_cell(**target_over):
    return {
        "cell_name": "m",
        "source": {"type": "session_staging", "dsn": "postgresql://x/y",
                   "staging_table": "chunkshop_staging", "mode": "realtime"},
        "chunker": {"type": "fixed_overlap", "max_words": 200, "overlap_words": 20},
        "embedder": {"type": "fastembed", "model_name": "Xenova/bge-small-en-v1.5-int8", "dim": 384},
        "target": {"type": "postgres", "dsn": "postgresql://x/y",
                   "database": "mem", "table": "t", "mode": "create_if_missing", **target_over},
    }


def test_session_staging_source_parses():
    c = CellConfig(**_min_cell())
    assert c.source.type == "session_staging"
    assert c.source.mode == "realtime"
    assert c.source.min_age_seconds == 3600
    assert c.source.staging_schema == "public"


def test_session_staging_rejects_bad_table_ident():
    cell = _min_cell()
    cell["source"]["staging_table"] = "bad-name;DROP"
    with pytest.raises(ValidationError, match="staging_table"):
        CellConfig(**cell)


def test_session_staging_rejects_unknown_mode():
    cell = _min_cell()
    cell["source"]["mode"] = "whenever"
    with pytest.raises(ValidationError):
        CellConfig(**cell)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_config.py::test_session_staging_source_parses -v`
Expected: FAIL — `session_staging` not a valid source discriminator.

- [ ] **Step 3: Add the model and register it**

In `config.py`, add after `JsonCorpusSource` (the model):

```python
class SessionStagingSource(_DsnResolvable):
    type: Literal["session_staging"]
    staging_table: str
    staging_schema: str = "public"
    mode: Literal["realtime", "consolidate"]
    min_age_seconds: int = 3600
    max_sessions: Optional[int] = None

    @field_validator("staging_table", "staging_schema")
    @classmethod
    def _safe_ident(cls, v):
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(f"staging_table/staging_schema must match ^[a-z_][a-z0-9_]*$, got {v!r}")
        return v
```

Add `SessionStagingSource` to the `SourceConfig` Union member list (line ~167), keeping `Field(discriminator="type")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_memory_config.py
git commit -m "feat(memory): SessionStagingSource config model"
```

---

### Task 2: Config — `SessionEpisodeFramerConfig`

**Files:**
- Modify: `python/src/chunkshop/config.py` (add near `JSONPathFramerConfig` ~line 418; add to `FramerConfig` union ~line 437)
- Test: `python/tests/chunkshop/test_memory_config.py`

- [ ] **Step 1: Write the failing test** (append to the same test file)

```python
def test_session_episode_framer_defaults():
    cell = _min_cell()
    cell["framer"] = {"type": "session_episode"}
    c = CellConfig(**cell)
    assert c.framer.type == "session_episode"
    assert c.framer.max_gap_seconds == 1800
    assert c.framer.max_turns == 40
    assert c.framer.boundary_on_tool is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_config.py::test_session_episode_framer_defaults -v`
Expected: FAIL — `session_episode` not a valid framer discriminator.

- [ ] **Step 3: Add the model and register it**

In `config.py` after `JSONPathFramerConfig`:

```python
class SessionEpisodeFramerConfig(_Base):
    type: Literal["session_episode"] = "session_episode"
    max_gap_seconds: int = 1800
    max_turns: int = 40
    max_words: int = 1200
    boundary_on_tool: bool = True
```

Add `SessionEpisodeFramerConfig` to the `FramerConfig` Union member list.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_config.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_memory_config.py
git commit -m "feat(memory): SessionEpisodeFramer config model"
```

---

### Task 3: Config — Consolidator union + `ConsolidationChunker`

**Files:**
- Modify: `python/src/chunkshop/config.py` (add after `SummarizerConfig` ~line 285; chunker after `SummaryEmbedChunker` ~line 331; register in `ChunkerConfig` union ~line 369 and add `model_rebuild()`)
- Test: `python/tests/chunkshop/test_memory_config.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_consolidation_chunker_parses():
    cell = _min_cell()
    cell["chunker"] = {
        "type": "consolidation",
        "base": {"type": "sentence_aware", "doc_type": "prose"},
        "consolidator": {"mode": "callable", "module": "chunkshop.consolidators.extractive"},
    }
    c = CellConfig(**cell)
    assert c.chunker.type == "consolidation"
    assert c.chunker.consolidator.mode == "callable"
    assert c.chunker.consolidator.function == "consolidate"
    assert c.chunker.fact_max_chars == 1200


def test_consolidation_passthrough_consolidator():
    cell = _min_cell()
    cell["chunker"] = {
        "type": "consolidation",
        "base": {"type": "sentence_aware", "doc_type": "prose"},
        "consolidator": {"mode": "passthrough"},
    }
    c = CellConfig(**cell)
    assert c.chunker.consolidator.mode == "passthrough"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_config.py::test_consolidation_chunker_parses -v`
Expected: FAIL — `consolidation` not a valid chunker discriminator.

- [ ] **Step 3: Add models and register**

In `config.py` after the `SummarizerConfig` union:

```python
class CallableConsolidator(_Base):
    """Import a module lazily; call ``function(text, **kwargs) -> dict``.

    The dict must be ``{"summary": str, "facts": [ {subject,predicate,object,
    support_span,confidence}, ... ]}``. Mirrors CallableSummarizer.
    """
    mode: Literal["callable"]
    module: str
    function: str = "consolidate"
    kwargs: dict = Field(default_factory=dict)


class PassthroughConsolidator(_Base):
    """Baseline: summary = episode text, facts = []. For A/B + no-LLM default off."""
    mode: Literal["passthrough"]


ConsolidatorConfig = Annotated[
    Union[CallableConsolidator, PassthroughConsolidator],
    Field(discriminator="mode"),
]
```

After `SummaryEmbedChunker`:

```python
class ConsolidationChunker(_Base):
    """Wrap a base chunker; emit episode chunks (summary-enriched embedded_content)
    + atomic fact chunks (kind='fact') via a user-wired consolidator callable."""
    type: Literal["consolidation"]
    base: "ChunkerConfig"
    consolidator: ConsolidatorConfig
    fact_max_chars: int = 1200
    max_chars: Optional[int] = None
    if_oversize: Optional["ChunkerConfig"] = None

    def effective_max_chars(self) -> Optional[int]:
        if self.max_chars is not None:
            return self.max_chars
        getter = getattr(self.base, "effective_max_chars", None)
        return getter() if getter else None
```

Add `ConsolidationChunker` to the `ChunkerConfig` Union member list and add `ConsolidationChunker.model_rebuild()` alongside the other `model_rebuild()` calls.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_config.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_memory_config.py
git commit -m "feat(memory): Consolidator union + ConsolidationChunker config"
```

---

### Task 4: Config — `MemoryConfig` on `TargetConfig`

**Files:**
- Modify: `python/src/chunkshop/config.py` (add `MemoryConfig` before `TargetConfig` ~line 578; add `memory` field to `TargetConfig`)
- Test: `python/tests/chunkshop/test_memory_config.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_target_memory_block():
    c = CellConfig(**_min_cell(memory={"tier": "consolidated", "supersede": True}))
    assert c.target.memory.tier == "consolidated"
    assert c.target.memory.supersede is True
    assert c.target.memory.namespace is None


def test_target_without_memory_block_is_none():
    c = CellConfig(**_min_cell())
    assert c.target.memory is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_config.py::test_target_memory_block -v`
Expected: FAIL — `TargetConfig` has no `memory` field (`extra="forbid"`).

- [ ] **Step 3: Add the model and field**

Before `TargetConfig`:

```python
class MemoryConfig(_Base):
    tier: Literal["provisional", "consolidated"]
    supersede: bool = False
    namespace: Optional[str] = None

    @field_validator("namespace")
    @classmethod
    def _safe_ns(cls, v):
        if v is None:
            return v
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(f"namespace must match ^[a-z_][a-z0-9_]*$, got {v!r}")
        return v
```

In `TargetConfig` add the field:

```python
    memory: Optional[MemoryConfig] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_config.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/test_memory_config.py
git commit -m "feat(memory): MemoryConfig block on TargetConfig"
```

---

### Task 5: Staging API — `chunkshop.memory`

**Files:**
- Create: `python/src/chunkshop/memory/__init__.py`
- Create: `python/src/chunkshop/memory/staging.py`
- Test: `python/tests/chunkshop/test_memory_staging.py`

- [ ] **Step 1: Write the failing test**

```python
"""Integration tests for chunkshop.memory staging API."""
import os, psycopg, pytest
DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"

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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_memory CASCADE")
        conn.commit()


from chunkshop.memory import stage_event, stage_events, ensure_staging_table, prune_staging

T = dict(table="evt", schema="chunkshop_test_memory")


def _count(dsn):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunkshop_test_memory.evt")
        return cur.fetchone()[0]


def test_stage_event_and_idempotent(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, **T)
    eid = stage_event(dsn, session_id="s1", role="user", content="hi", ts=None, **T)
    assert isinstance(eid, str) and eid
    # same logical event re-staged → no duplicate
    stage_event(dsn, session_id="s1", role="user", content="hi", ts=None, event_id=eid, **T)
    assert _count(dsn) == 1


def test_stage_events_bulk(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, **T)
    n = stage_events(dsn, [
        {"session_id": "s1", "role": "user", "content": "a"},
        {"session_id": "s1", "role": "assistant", "content": "b", "tool": "bash"},
    ], **T)
    assert n == 2 and _count(dsn) == 2


def test_prune_staging_only_consolidated(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, **T)
    stage_event(dsn, session_id="s1", role="user", content="x", ts=None, **T)
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("UPDATE chunkshop_test_memory.evt SET consumed='{\"consolidated\": \"2000-01-01T00:00:00Z\"}', staged_at='2000-01-01'")
        c.commit()
    removed = prune_staging(dsn, older_than="2020-01-01", only_consolidated=True, **T)
    assert removed == 1 and _count(dsn) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_staging.py -v`
Expected: FAIL — `ModuleNotFoundError: chunkshop.memory`.

- [ ] **Step 3: Implement**

`python/src/chunkshop/memory/__init__.py`:

```python
"""chunkshop agent-memory staging API.

The only 'live' touchpoint: an external capture layer pushes session events
into a chunkshop-owned append-only staging table. Two scheduled cells
(memory/realtime.yaml, memory/consolidate.yaml) consume it. This module is
deliberately NOT chunkshop.Pipeline (which requires an inline source).
"""
from chunkshop.memory.staging import (
    stage_event, stage_events, ensure_staging_table, prune_staging,
)

__all__ = ["stage_event", "stage_events", "ensure_staging_table", "prune_staging"]
```

`python/src/chunkshop/memory/staging.py`:

```python
from __future__ import annotations
import hashlib
import re
from typing import Optional

import psycopg
from psycopg import sql

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ident(schema: str, table: str) -> sql.Composed:
    for v in (schema, table):
        if not _IDENT.match(v):
            raise ValueError(f"identifier must match ^[a-z_][a-z0-9_]*$, got {v!r}")
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def _event_id(session_id: str, seq, ts, content: str) -> str:
    key = f"{session_id}\x00{seq if seq is not None else ts}\x00{content}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def ensure_staging_table(dsn: str, *, table: str, schema: str = "public") -> None:
    fq = _ident(schema, table)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        cur.execute(sql.SQL(
            "CREATE TABLE IF NOT EXISTS {fq} ("
            " event_id text PRIMARY KEY,"
            " session_id text NOT NULL,"
            " seq bigint,"
            " role text,"
            " content text NOT NULL,"
            " tool text,"
            " outcome text,"
            " event_ts timestamptz,"
            " staged_at timestamptz NOT NULL DEFAULT now(),"
            " consumed jsonb NOT NULL DEFAULT '{{}}'::jsonb,"
            " metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb)"
        ).format(fq=fq))
        cur.execute(sql.SQL(
            "CREATE INDEX IF NOT EXISTS {ix} ON {fq} (session_id, seq)"
        ).format(ix=sql.Identifier(f"{table}_session_seq"), fq=fq))
        cur.execute(sql.SQL(
            "CREATE INDEX IF NOT EXISTS {ix} ON {fq} (staged_at)"
        ).format(ix=sql.Identifier(f"{table}_staged_at"), fq=fq))
        conn.commit()


def stage_event(dsn: str, *, session_id: str, role: str, content: str,
                 ts=None, seq: Optional[int] = None, tool: Optional[str] = None,
                 outcome: Optional[str] = None, event_id: Optional[str] = None,
                 metadata: Optional[dict] = None,
                 table: str, schema: str = "public") -> str:
    eid = event_id or _event_id(session_id, seq, ts, content)
    fq = _ident(schema, table)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL(
            "INSERT INTO {fq} (event_id, session_id, seq, role, content, tool,"
            " outcome, event_ts, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"
            " ON CONFLICT (event_id) DO NOTHING"
        ).format(fq=fq), (eid, session_id, seq, role, content, tool, outcome, ts,
                          psycopg.types.json.Json(metadata or {})))
        conn.commit()
    return eid


def stage_events(dsn: str, events: list[dict], *, table: str, schema: str = "public") -> int:
    fq = _ident(schema, table)
    rows = []
    for e in events:
        eid = e.get("event_id") or _event_id(
            e["session_id"], e.get("seq"), e.get("ts"), e["content"])
        rows.append((eid, e["session_id"], e.get("seq"), e.get("role"), e["content"],
                     e.get("tool"), e.get("outcome"), e.get("ts"),
                     psycopg.types.json.Json(e.get("metadata") or {})))
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.executemany(sql.SQL(
            "INSERT INTO {fq} (event_id, session_id, seq, role, content, tool,"
            " outcome, event_ts, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"
            " ON CONFLICT (event_id) DO NOTHING"
        ).format(fq=fq), rows)
        conn.commit()
    return len(rows)


def prune_staging(dsn: str, *, older_than: str, only_consolidated: bool = True,
                  table: str, schema: str = "public") -> int:
    fq = _ident(schema, table)
    cond = sql.SQL("staged_at < %s")
    if only_consolidated:
        cond = sql.SQL("staged_at < %s AND consumed ? 'consolidated'")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DELETE FROM {fq} WHERE {cond}").format(fq=fq, cond=cond),
                    (older_than,))
        n = cur.rowcount
        conn.commit()
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_staging.py -v`
Expected: PASS (3 tests), or SKIP if no Postgres.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/memory/ python/tests/chunkshop/test_memory_staging.py
git commit -m "feat(memory): staging API (stage_event/stage_events/ensure/prune)"
```

---

### Task 6: `SessionStagingSource` — read logic (SQLite unit)

**Files:**
- Create: `python/src/chunkshop/sources/session_staging.py`
- Modify: `python/src/chunkshop/sources/__init__.py` (import + `load_source` branch + `__all__`)
- Test: `python/tests/chunkshop/test_session_staging_source.py`

The source connects through `PostgresBackend` in production, but its row→Document logic is pure and is unit-tested by injecting an in-memory event list (no DB). Production path is covered by the e2e integration test (Task 14).

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for SessionStagingSource row→Document logic (no DB)."""
from chunkshop.config import SessionStagingSource as Cfg
from chunkshop.sources.session_staging import SessionStagingSource


def _cfg(mode):
    return Cfg(type="session_staging", dsn="postgresql://x/y",
              staging_table="evt", staging_schema="public", mode=mode,
              min_age_seconds=3600)


def _rows():
    # (event_id, session_id, seq, role, content, tool, outcome, event_ts)
    return [
        ("e1", "s1", 1, "user", "hello", None, None, 1000.0),
        ("e2", "s1", 2, "assistant", "hi back", "bash", "ok", 1005.0),
        ("e3", "s2", 1, "user", "other", None, None, 2000.0),
    ]


def test_groups_one_document_per_session():
    src = SessionStagingSource(_cfg("consolidate"))
    docs = list(src._documents_from_rows(_rows()))
    assert {d.id for d in docs} == {"s1", "s2"}
    s1 = next(d for d in docs if d.id == "s1")
    assert s1.metadata["session_id"] == "s1"
    assert s1.metadata["event_count"] == 2
    assert len(s1.metadata["_session_events"]) == 2
    # events carried in order
    assert [e["content"] for e in s1.metadata["_session_events"]] == ["hello", "hi back"]
    # content is a readable, deterministic serialization
    assert "hello" in s1.content and "hi back" in s1.content


def test_events_sorted_by_seq_then_ts():
    rows = [("e2", "s1", 2, "assistant", "second", None, None, 5.0),
            ("e1", "s1", 1, "user", "first", None, None, 9.0)]
    src = SessionStagingSource(_cfg("realtime"))
    doc = list(src._documents_from_rows(rows))[0]
    assert [e["content"] for e in doc.metadata["_session_events"]] == ["first", "second"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_session_staging_source.py -v`
Expected: FAIL — module `chunkshop.sources.session_staging` does not exist.

- [ ] **Step 3: Implement**

`python/src/chunkshop/sources/session_staging.py`:

```python
from __future__ import annotations
from typing import Iterator

from chunkshop.backends.postgres import PostgresBackend
from chunkshop.config import SessionStagingSource as Cfg
from chunkshop.sources.base import Document

_SELECT = (
    "SELECT event_id, session_id, seq, role, content, tool, outcome,"
    " extract(epoch FROM coalesce(event_ts, staged_at)) "
    "FROM {schema}.{table} {where} ORDER BY session_id, seq NULLS LAST"
)


class SessionStagingSource:
    """Yield one Document per session from the chunkshop staging table.

    realtime mode: sessions with events newer than consumed.realtime.
    consolidate mode: sessions quiet for >= min_age_seconds with new events
    since consumed.consolidated. Advances the per-session watermark on yield.
    """

    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = PostgresBackend(**cfg.backend_dsn_kwargs())

    # -- pure: rows -> Documents (unit-tested) ------------------------------
    def _documents_from_rows(self, rows) -> Iterator[Document]:
        by_session: dict[str, list[dict]] = {}
        for (eid, sid, seq, role, content, tool, outcome, ts) in rows:
            by_session.setdefault(sid, []).append(
                {"event_id": eid, "seq": seq, "role": role, "content": content,
                 "tool": tool, "outcome": outcome, "ts": ts})
        for sid, evs in by_session.items():
            evs.sort(key=lambda e: (e["seq"] is None, e["seq"], e["ts"]))
            lines = []
            for e in evs:
                tag = e["role"] or "event"
                if e["tool"]:
                    tag += f"/{e['tool']}"
                lines.append(f"[{tag}] {e['content']}")
            yield Document(
                id=sid,
                content="\n".join(lines),
                title=None,
                metadata={
                    "session_id": sid,
                    "namespace": None,
                    "event_count": len(evs),
                    "first_ts": evs[0]["ts"],
                    "last_ts": evs[-1]["ts"],
                    "mode": self.cfg.mode,
                    "_session_events": evs,
                },
            )

    # -- production: query staging, yield, advance watermark ----------------
    def iter_documents(self) -> Iterator[Document]:
        if self.cfg.mode == "realtime":
            where = ("WHERE coalesce(consumed->>'realtime','') = '' "
                     "OR staged_at > (consumed->>'realtime')::timestamptz")
            wm = "realtime"
        else:
            where = ("WHERE coalesce(event_ts, staged_at) "
                     f"< now() - interval '{int(self.cfg.min_age_seconds)} seconds' "
                     "AND (coalesce(consumed->>'consolidated','') = '' "
                     "OR staged_at > (consumed->>'consolidated')::timestamptz)")
            wm = "consolidated"
        q = _SELECT.format(schema=self.cfg.staging_schema,
                           table=self.cfg.staging_table, where=where)
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(q)
            rows = cur.fetchall()
            sessions = {r[1] for r in rows}
            for doc in self._documents_from_rows(rows):
                yield doc
            if sessions:
                cur.execute(
                    f"UPDATE {self.cfg.staging_schema}.{self.cfg.staging_table} "
                    f"SET consumed = consumed || jsonb_build_object(%s, now()::text) "
                    f"WHERE session_id = ANY(%s)",
                    (wm, list(sessions)))
                conn.commit()
```

In `sources/__init__.py`: add `from chunkshop.config import (... SessionStagingSource as SessionStagingCfg, ...)`, `from chunkshop.sources.session_staging import SessionStagingSource`, a branch `if isinstance(cfg, SessionStagingCfg): return SessionStagingSource(cfg)` (place it before the `InlineCfg` branch), and add `"SessionStagingSource"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_session_staging_source.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sources/session_staging.py python/src/chunkshop/sources/__init__.py python/tests/chunkshop/test_session_staging_source.py
git commit -m "feat(memory): SessionStagingSource (row→Document grouping + watermark)"
```

---

### Task 7: `SessionEpisodeFramer` (pure, stateless)

**Files:**
- Create: `python/src/chunkshop/framers/session_episode.py`
- Modify: `python/src/chunkshop/framers/__init__.py` (import + branch + `__all__`)
- Test: `python/tests/chunkshop/test_session_episode_framer.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for SessionEpisodeFramer (pure/stateless)."""
from chunkshop.config import SessionEpisodeFramerConfig as Cfg
from chunkshop.framers.session_episode import SessionEpisodeFramer
from chunkshop.sources.base import Document


def _doc(events):
    return Document(id="s1", content="x", title=None,
                    metadata={"session_id": "s1", "_session_events": events})


def test_time_gap_splits_episodes():
    evs = [{"role": "user", "content": "a", "ts": 0.0, "tool": None},
           {"role": "assistant", "content": "b", "ts": 10.0, "tool": None},
           {"role": "user", "content": "c", "ts": 99999.0, "tool": None}]
    fr = SessionEpisodeFramer(Cfg(max_gap_seconds=1800))
    out = fr.frame(_doc(evs))
    assert len(out) == 2
    assert out[0].metadata["frame_seq"] == 0
    assert out[1].metadata["frame_seq"] == 1
    assert out[0].metadata["framer"] == "session_episode"
    assert "a" in out[0].content and "c" in out[1].content
    assert len(out[0].metadata["_episode_events"]) == 2


def test_single_episode_when_contiguous():
    evs = [{"role": "user", "content": "a", "ts": 0.0, "tool": None},
           {"role": "assistant", "content": "b", "ts": 5.0, "tool": None}]
    out = SessionEpisodeFramer(Cfg()).frame(_doc(evs))
    assert len(out) == 1 and out[0].metadata["episode_turn_span"] == 2


def test_empty_session_yields_nothing():
    assert SessionEpisodeFramer(Cfg()).frame(_doc([])) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_session_episode_framer.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

`python/src/chunkshop/framers/session_episode.py`:

```python
from __future__ import annotations
from dataclasses import replace

from chunkshop.config import SessionEpisodeFramerConfig as Cfg
from chunkshop.sources.base import Document


class SessionEpisodeFramer:
    """Split one session Document into episode Documents. Stateless, no I/O.

    Boundaries: time gap > max_gap_seconds, OR turn count >= max_turns, OR
    word count >= max_words, OR (boundary_on_tool and a tool event follows a
    non-tool event). Reads metadata['_session_events'] (ordered)."""

    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def frame(self, raw: Document) -> list[Document]:
        events = list((raw.metadata or {}).get("_session_events") or [])
        if not events:
            return []
        episodes: list[list[dict]] = [[]]
        words = 0
        for i, e in enumerate(events):
            cur = episodes[-1]
            if cur:
                prev = cur[-1]
                gap = (e.get("ts") or 0) - (prev.get("ts") or 0)
                tool_boundary = (self.cfg.boundary_on_tool and e.get("tool")
                                 and not prev.get("tool"))
                if (gap > self.cfg.max_gap_seconds
                        or len(cur) >= self.cfg.max_turns
                        or words >= self.cfg.max_words
                        or tool_boundary):
                    episodes.append([])
                    cur = episodes[-1]
                    words = 0
            cur.append(e)
            words += len((e.get("content") or "").split())
        base_meta = {k: v for k, v in (raw.metadata or {}).items()
                     if k != "_session_events"}
        out: list[Document] = []
        for seq, evs in enumerate(e for e in episodes if e):
            lines = []
            for ev in evs:
                tag = ev.get("role") or "event"
                if ev.get("tool"):
                    tag += f"/{ev['tool']}"
                lines.append(f"[{tag}] {ev.get('content','')}")
            meta = dict(base_meta)
            meta.update({
                "framer": "session_episode",
                "frame_seq": seq,
                "episode_start_ts": evs[0].get("ts"),
                "episode_end_ts": evs[-1].get("ts"),
                "episode_turn_span": len(evs),
                "_episode_events": evs,
            })
            out.append(Document(id=raw.id, content="\n".join(lines),
                                title=raw.title, metadata=meta))
        return out
```

In `framers/__init__.py`: add the import, extend the deferred config import to include `SessionEpisodeFramerConfig`, add `if isinstance(cfg, SessionEpisodeFramerConfig): return SessionEpisodeFramer(cfg)`, and add to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_session_episode_framer.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/framers/session_episode.py python/src/chunkshop/framers/__init__.py python/tests/chunkshop/test_session_episode_framer.py
git commit -m "feat(memory): SessionEpisodeFramer (stateless episode segmentation)"
```

---

### Task 8: Consolidator factory + protocol

**Files:**
- Create: `python/src/chunkshop/chunkers/_consolidator.py`
- Test: `python/tests/chunkshop/test_consolidator_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for build_consolidator (mirrors _summarizer.build_summarizer)."""
import pytest
from chunkshop.config import CallableConsolidator, PassthroughConsolidator
from chunkshop.chunkers._consolidator import build_consolidator


def test_passthrough_returns_summary_and_no_facts():
    fn = build_consolidator(PassthroughConsolidator(mode="passthrough"))
    out = fn("episode text", {})
    assert out["summary"] == "episode text"
    assert out["facts"] == []


def test_callable_invokes_module_function(tmp_path, monkeypatch):
    import sys, types
    mod = types.ModuleType("fake_consolidator")
    mod.consolidate = lambda text, **kw: {
        "summary": "S:" + text[:3],
        "facts": [{"subject": "a", "predicate": "is", "object": "b",
                   "support_span": "a is b", "confidence": 0.9}]}
    sys.modules["fake_consolidator"] = mod
    fn = build_consolidator(CallableConsolidator(
        mode="callable", module="fake_consolidator"))
    out = fn("hello world", {})
    assert out["summary"] == "S:hel"
    assert out["facts"][0]["predicate"] == "is"


def test_callable_bad_module_raises_actionable():
    fn_cfg = CallableConsolidator(mode="callable", module="nope.not.here")
    with pytest.raises(RuntimeError, match="could not import"):
        build_consolidator(fn_cfg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_consolidator_dispatch.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

`python/src/chunkshop/chunkers/_consolidator.py`:

```python
"""Consolidator dispatch: ConsolidatorConfig -> (text, meta) -> dict.

Returned dict: {"summary": str, "facts": [ {subject,predicate,object,
support_span,confidence}, ... ]}. Mirrors chunkers/_summarizer.build_summarizer:
lazy import on the callable path, actionable RuntimeError strings, chunkshop
core never imports a consolidator unless YAML asks for one.
"""
from __future__ import annotations
from importlib import import_module
from typing import Callable

from chunkshop.config import CallableConsolidator, PassthroughConsolidator

ConsolidatorFn = Callable[[str, dict], dict]


def _normalize(raw: dict) -> dict:
    facts = []
    for f in (raw.get("facts") or []):
        facts.append({
            "subject": f.get("subject"),
            "predicate": f.get("predicate"),
            "object": f.get("object"),
            "support_span": f.get("support_span") or "",
            "confidence": f.get("confidence"),
        })
    return {"summary": raw.get("summary") or "", "facts": facts}


def build_consolidator(cfg) -> ConsolidatorFn:
    if isinstance(cfg, PassthroughConsolidator):
        return lambda text, meta: {"summary": text, "facts": []}

    if isinstance(cfg, CallableConsolidator):
        try:
            mod = import_module(cfg.module)
        except ImportError as exc:
            raise RuntimeError(
                f"callable consolidator: could not import {cfg.module!r}: {exc}. "
                f"Install it and retry."
            ) from exc
        fn = getattr(mod, cfg.function, None)
        if fn is None:
            raise RuntimeError(
                f"callable consolidator: module {cfg.module!r} has no attribute "
                f"{cfg.function!r}")
        kwargs = dict(cfg.kwargs)

        def _callable(text: str, meta: dict) -> dict:
            return _normalize(fn(text, **kwargs) or {})

        return _callable

    raise ValueError(f"unknown consolidator config: {type(cfg).__name__}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_consolidator_dispatch.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/chunkers/_consolidator.py python/tests/chunkshop/test_consolidator_dispatch.py
git commit -m "feat(memory): build_consolidator factory + protocol"
```

---

### Task 9: Default extractive consolidator

**Files:**
- Create: `python/src/chunkshop/consolidators/__init__.py`
- Create: `python/src/chunkshop/consolidators/extractive.py`
- Test: `python/tests/chunkshop/test_extractive_consolidator.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for the zero-network default extractive consolidator."""
from chunkshop.consolidators.extractive import consolidate


def test_empty_input():
    out = consolidate("")
    assert out == {"summary": "", "facts": []}


def test_summary_is_nonempty_and_facts_are_propositions():
    text = ("[user] We decided to drop Redis and use Postgres for the queue.\n"
            "[assistant] Acknowledged. Postgres LISTEN/NOTIFY will replace it.")
    out = consolidate(text, max_sentences=2)
    assert isinstance(out["summary"], str) and out["summary"]
    # extractive default: propositions present, triples sparse (subject None ok)
    assert len(out["facts"]) >= 1
    f = out["facts"][0]
    assert f["support_span"] and isinstance(f["support_span"], str)
    assert "subject" in f and "predicate" in f and "object" in f
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_extractive_consolidator.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

`python/src/chunkshop/consolidators/__init__.py`:

```python
"""Origin-agnostic consolidator shims.

Each module exposes ``consolidate(text: str, **kwargs) -> dict`` returning
``{"summary": str, "facts": [ {subject,predicate,object,support_span,
confidence}, ... ]}`` so a user YAML references them via
``module: chunkshop.consolidators.<name>``. The default `extractive` is
zero-network (sentence split + lightweight proposition extraction); an LLM
consolidator is user-supplied and wired the same way.
"""
```

`python/src/chunkshop/consolidators/extractive.py`:

```python
"""Zero-network default consolidator.

summary  = first N non-trivial sentences (stand-in for an abstractive summary;
           users wire an LLM callable for better summaries).
facts    = one proposition per sentence that looks declarative; triple fields
           (subject/predicate/object) are left None under the extractive
           default — by design the soft-invalidate path then no-ops (spec O8 /
           §5). support_span carries the proposition text (the embedded body).
"""
from __future__ import annotations
import re

_SENT = re.compile(r"(?<=[.!?])\s+")
_TAG = re.compile(r"^\[[^\]]+\]\s*")


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = _TAG.sub("", line).strip()
        if not line:
            continue
        out.extend(s.strip() for s in _SENT.split(line) if s.strip())
    return out


def consolidate(text: str, *, max_sentences: int = 4,
                 min_words: int = 4, **_kw) -> dict:
    if not text or not text.strip():
        return {"summary": "", "facts": []}
    sents = _sentences(text)
    summary = " ".join(sents[:max_sentences])
    facts = []
    for s in sents:
        if len(s.split()) < min_words:
            continue
        facts.append({"subject": None, "predicate": None, "object": None,
                      "support_span": s, "confidence": None})
    return {"summary": summary, "facts": facts}


__all__ = ["consolidate"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_extractive_consolidator.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/consolidators/ python/tests/chunkshop/test_extractive_consolidator.py
git commit -m "feat(memory): zero-network default extractive consolidator"
```

---

### Task 10: `ConsolidationChunker`

**Files:**
- Create: `python/src/chunkshop/chunkers/consolidation.py`
- Modify: `python/src/chunkshop/chunkers/__init__.py` (import, config import, branch, `__all__`)
- Test: `python/tests/chunkshop/test_consolidation_chunker.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for ConsolidationChunker (episode + fact emission, resilience)."""
from chunkshop.config import (ConsolidationChunker as Cfg, SentenceAwareChunker,
                              CallableConsolidator, PassthroughConsolidator)
from chunkshop.chunkers import load_chunker
from chunkshop.sources.base import Document
import sys, types


def _episode_doc():
    return Document(id="s1", content="[user] a b c d e. f g h i j.",
                    title=None,
                    metadata={"session_id": "s1", "frame_seq": 0,
                              "_episode_events": [{"role": "user",
                                  "content": "a b c d e. f g h i j.", "ts": 1.0}]})


def _cfg(consolidator):
    return Cfg(type="consolidation",
               base=SentenceAwareChunker(type="sentence_aware", doc_type="prose"),
               consolidator=consolidator)


def test_emits_episode_and_fact_chunks():
    mod = types.ModuleType("fk")
    mod.consolidate = lambda text, **kw: {"summary": "SUM",
        "facts": [{"subject": "x", "predicate": "p", "object": "y",
                   "support_span": "x p y", "confidence": 0.5}]}
    sys.modules["fk"] = mod
    ch = load_chunker(_cfg(CallableConsolidator(mode="callable", module="fk")))
    chunks = ch.chunk(_episode_doc())
    kinds = [c.metadata.get("kind") for c in chunks]
    assert "episode" in kinds and "fact" in kinds
    ep = next(c for c in chunks if c.metadata["kind"] == "episode")
    assert ep.embedded_content == "SUM"
    assert ep.original_content  # raw episode text retained
    fa = next(c for c in chunks if c.metadata["kind"] == "fact")
    assert fa.embedded_content == "x p y"
    assert fa.metadata["subject"] == "x" and fa.metadata["predicate"] == "p"
    assert fa.metadata["source_chunk_seq"] == ep.seq_num
    # transient keys not persisted
    assert "_episode_events" not in ep.metadata


def test_callable_failure_degrades_to_passthrough():
    mod = types.ModuleType("boom")
    def _raise(text, **kw):
        raise RuntimeError("llm down")
    mod.consolidate = _raise
    sys.modules["boom"] = mod
    ch = load_chunker(_cfg(CallableConsolidator(mode="callable", module="boom")))
    chunks = ch.chunk(_episode_doc())
    assert [c.metadata["kind"] for c in chunks] == ["episode"]
    ep = chunks[0]
    assert ep.metadata.get("consolidation_error")
    assert ep.embedded_content == ep.original_content  # passthrough


def test_fact_support_span_length_capped():
    big = "w " * 5000
    mod = types.ModuleType("big")
    mod.consolidate = lambda text, **kw: {"summary": "s",
        "facts": [{"subject": None, "predicate": None, "object": None,
                   "support_span": big, "confidence": None}]}
    sys.modules["big"] = mod
    cfg = _cfg(CallableConsolidator(mode="callable", module="big"))
    cfg = cfg.model_copy(update={"fact_max_chars": 50})
    ch = load_chunker(cfg)
    fa = next(c for c in ch.chunk(_episode_doc()) if c.metadata["kind"] == "fact")
    assert len(fa.embedded_content) <= 50 and fa.metadata["truncated"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_consolidation_chunker.py -v`
Expected: FAIL — module `chunkshop.chunkers.consolidation` does not exist.

- [ ] **Step 3: Implement**

`python/src/chunkshop/chunkers/consolidation.py`:

```python
"""ConsolidationChunker — emit episode + atomic-fact chunks via a user-wired
consolidator. Mirrors SummaryEmbedChunker's wrap-base + callable pattern.

Per spec C5/O4: on consolidator failure, degrade to a passthrough episode
chunk (raw text, zero facts, metadata.consolidation_error) — never raise, so
one poisoned session can't abort the nightly cell. Facts are length-capped
(metadata.truncated), not split (splitting breaks the proposition)."""
from __future__ import annotations
import logging
from dataclasses import replace

from chunkshop.chunkers.base import Chunk, Chunker
from chunkshop.chunkers._consolidator import build_consolidator
from chunkshop.config import ConsolidationChunker as Cfg
from chunkshop.sources.base import Document

logger = logging.getLogger(__name__)


def _strip_transient(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if not k.startswith("_")}


class ConsolidationChunker:
    def __init__(self, cfg: Cfg, base: Chunker, build_chunker=None):
        self.cfg = cfg
        self.base = base
        self._consolidate = build_consolidator(cfg.consolidator)
        self._mode = cfg.consolidator.mode

    def chunk(self, doc: Document) -> list[Chunk]:
        base_chunks = self.base.chunk(doc)
        episode_text = "\n".join(c.original_content for c in base_chunks) or doc.content
        meta = _strip_transient(dict(doc.metadata or {}))
        seq = 0
        try:
            result = self._consolidate(episode_text, dict(doc.metadata or {}))
        except Exception as exc:  # O4: degrade, never raise
            logger.warning("consolidator failed for doc %s: %s", doc.id, exc)
            em = {**meta, "kind": "episode", "consolidation_error": str(exc),
                  "consolidator": self._mode}
            return [Chunk(doc_id=doc.id, seq_num=0,
                          original_content=episode_text,
                          embedded_content=episode_text, metadata=em)]
        out: list[Chunk] = []
        ep_meta = {**meta, "kind": "episode", "consolidator": self._mode}
        episode = Chunk(doc_id=doc.id, seq_num=seq,
                        original_content=episode_text,
                        embedded_content=result["summary"] or episode_text,
                        metadata=ep_meta)
        out.append(episode)
        seq += 1
        cap = self.cfg.fact_max_chars
        for f in result["facts"]:
            span = f["support_span"] or ""
            truncated = len(span) > cap
            if truncated:
                span = span[:cap]
            fm = {**meta, "kind": "fact",
                  "subject": f["subject"], "predicate": f["predicate"],
                  "object": f["object"], "support_span": span,
                  "confidence": f["confidence"], "truncated": truncated,
                  "source_chunk_seq": episode.seq_num,
                  "consolidator": self._mode}
            out.append(Chunk(doc_id=doc.id, seq_num=seq,
                             original_content=span, embedded_content=span,
                             metadata=fm))
            seq += 1
        return out
```

In `chunkers/__init__.py`: add `from chunkshop.chunkers.consolidation import ConsolidationChunker`; add `ConsolidationChunker as ConsolidationCfg` to the `from chunkshop.config import (...)` block; add a branch mirroring `SummaryEmbedCfg`:

```python
    if isinstance(cfg, ConsolidationCfg):
        base = load_chunker(cfg.base, main_embedder=main_embedder,
                            shared_boundary_model=shared_boundary_model)
        return ConsolidationChunker(cfg, base, build_chunker=_build)
```

(No `__all__` change needed — it only exports `Chunk, Chunker, load_chunker`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_consolidation_chunker.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/chunkers/consolidation.py python/src/chunkshop/chunkers/__init__.py python/tests/chunkshop/test_consolidation_chunker.py
git commit -m "feat(memory): ConsolidationChunker (episode+fact emission, O4 resilience)"
```

---

### Task 11: `MemorySink` — stamping + DDL (integration)

**Files:**
- Create: `python/src/chunkshop/sinks/memory_pg.py`
- Modify: `python/src/chunkshop/sinks/__init__.py` (return `MemorySink` when `target.memory` set)
- Test: `python/tests/chunkshop/test_memory_sink.py`

- [ ] **Step 1: Write the failing test**

```python
"""Integration: MemorySink stamps cell-level promoted fields + DDL."""
import os, psycopg, pytest, numpy as np
DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"

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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_memory CASCADE")
        conn.commit()

from chunkshop.config import TargetConfig
from chunkshop.chunkers.base import Chunk
from chunkshop.sinks import load_sink

PROMO = [{"path": "kind", "type": "text"}, {"path": "session_id", "type": "text"},
         {"path": "tier", "type": "text"}, {"path": "namespace", "type": "text"},
         {"path": "subject", "type": "text"}, {"path": "support_span", "type": "text"},
         {"path": "recorded_at", "type": "timestamptz"},
         {"path": "effective_from", "type": "timestamptz"},
         {"path": "retracted", "type": "boolean"}]


def _target(**ov):
    k = dict(type="postgres", dsn_env=DSN_ENV, database="chunkshop_test_memory",
             table="mem", mode="create_if_missing", source_tag="ns1",
             promote_metadata=PROMO,
             memory={"tier": "provisional", "supersede": False})
    k.update(ov)
    return TargetConfig(**k)


def test_memorysink_returned_and_stamps(ensure_pg):
    sink = load_sink(_target(), embed_dim=3)
    assert sink.__class__.__name__ == "MemorySink"
    sink.create_table()
    ch = Chunk(doc_id="s1", seq_num=0, original_content="raw",
               embedded_content="raw", metadata={"session_id": "s1"})
    sink.write_document("s1", [ch], np.array([[0.1, 0.2, 0.3]]), [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute("SELECT kind, tier, namespace, session_id, recorded_at "
                    "FROM chunkshop_test_memory.mem")
        row = cur.fetchone()
    assert row[0] == "episode" and row[1] == "provisional"
    assert row[2] == "ns1" and row[3] == "s1" and row[4] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_sink.py::test_memorysink_returned_and_stamps -v`
Expected: FAIL — `load_sink` returns `PgSink`, not `MemorySink`.

- [ ] **Step 3: Implement**

`python/src/chunkshop/sinks/memory_pg.py`:

```python
"""MemorySink — PgSink + agent-memory write semantics.

Owns the cell-level promoted fields so chunkers/framers stay memory-agnostic:
stamps tier/namespace/recorded_at unconditionally and defaults kind='episode'
when absent. Adds supersede (consolidated replaces provisional per session)
and soft-invalidate (contradicted prior facts -> retracted). Strips leftover
'_'-prefixed transient metadata keys before insert."""
from __future__ import annotations
import datetime as _dt
from dataclasses import replace

import numpy as np

from chunkshop.chunkers.base import Chunk
from chunkshop.sinks.pg import PgSink


class MemorySink(PgSink):
    def __init__(self, cfg, backend, embed_dim: int):
        super().__init__(cfg=cfg, backend=backend, embed_dim=embed_dim)
        self._mem = cfg.memory
        self._namespace = self._mem.namespace or cfg.source_tag
        self._recorded_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        self._superseded: set[str] = set()

    def _stamp(self, chunks: list[Chunk]) -> list[Chunk]:
        out = []
        for c in chunks:
            m = {k: v for k, v in c.metadata.items() if not k.startswith("_")}
            m.setdefault("kind", "episode")
            m["tier"] = self._mem.tier
            m["namespace"] = self._namespace
            m["recorded_at"] = self._recorded_at
            m.setdefault("effective_from", m.get("episode_end_ts"))
            out.append(replace(c, metadata=m))
        return out

    def write_document(self, doc_id, chunks, embeddings, tags_per_chunk) -> None:
        chunks = self._stamp(chunks)
        if (self._mem.supersede and self._mem.tier == "consolidated"
                and doc_id not in self._superseded):
            with self.backend.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self._fq()} WHERE doc_id = %s AND source = %s",
                    (doc_id, self.cfg.source_tag))
                conn.commit()
            self._superseded.add(doc_id)
        super().write_document(doc_id, chunks, embeddings, tags_per_chunk)
        self._invalidate(chunks)

    def _invalidate(self, chunks: list[Chunk]) -> None:
        facts = [c for c in chunks if c.metadata.get("kind") == "fact"
                 and c.metadata.get("subject") and c.metadata.get("predicate")]
        if not facts:
            return
        with self.backend.connect() as conn, conn.cursor() as cur:
            for c in facts:
                m = c.metadata
                cur.execute(
                    f"UPDATE {self._fq()} SET retracted = true, "
                    f"retracted_at = now(), effective_to = %s "
                    f"WHERE source = %s AND subject = %s AND predicate = %s "
                    f"AND effective_from < %s "
                    f"AND coalesce(retracted, false) = false",
                    (m.get("effective_from"), self.cfg.source_tag,
                     m.get("subject"), m.get("predicate"),
                     m.get("effective_from")))
            conn.commit()
```

In `sinks/__init__.py`, change the postgres branch:

```python
    if cfg.type == "postgres":
        backend = load_backend(name="postgres", **dsn_kwargs)
        if getattr(cfg, "memory", None) is not None:
            from chunkshop.sinks.memory_pg import MemorySink
            return MemorySink(cfg=cfg, backend=backend, embed_dim=embed_dim)
        return PgSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
```

Add `"MemorySink"` to `__all__`.

Note: promoted columns `retracted`, `retracted_at`, `effective_to`, `subject`, `predicate`, `effective_from` must be in the preset's `promote_metadata` for `_invalidate`'s `UPDATE` to resolve — the e2e preset (Task 13) declares the full set; this task's test declares the subset it asserts plus the ones `_invalidate` touches via the `PROMO` list (extend `PROMO` here to include `predicate`, `effective_to`, `retracted_at` so the UPDATE column references resolve).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_sink.py -v`
Expected: PASS (or SKIP without Postgres).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sinks/memory_pg.py python/src/chunkshop/sinks/__init__.py python/tests/chunkshop/test_memory_sink.py
git commit -m "feat(memory): MemorySink stamping + DDL + loader dispatch"
```

---

### Task 12: `MemorySink` — supersede + soft-invalidate (integration)

**Files:**
- Test: `python/tests/chunkshop/test_memory_sink_supersede.py`
- (No new source; exercises Task 11's `MemorySink`.)

- [ ] **Step 1: Write the failing test**

```python
"""Integration: supersede (scoped by source) + soft-invalidate + idempotency."""
import os, psycopg, pytest, numpy as np
DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"

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
        cur.execute("DROP SCHEMA IF EXISTS chunkshop_test_memory CASCADE")
        conn.commit()

from chunkshop.config import TargetConfig
from chunkshop.chunkers.base import Chunk
from chunkshop.sinks import load_sink

PROMO = [{"path": "kind", "type": "text"}, {"path": "session_id", "type": "text"},
         {"path": "tier", "type": "text"}, {"path": "namespace", "type": "text"},
         {"path": "subject", "type": "text"}, {"path": "predicate", "type": "text"},
         {"path": "object", "type": "text"}, {"path": "support_span", "type": "text"},
         {"path": "confidence", "type": "text"},
         {"path": "recorded_at", "type": "timestamptz"},
         {"path": "effective_from", "type": "timestamptz"},
         {"path": "effective_to", "type": "timestamptz"},
         {"path": "retracted", "type": "boolean"},
         {"path": "retracted_at", "type": "timestamptz"},
         {"path": "source_chunk_seq", "type": "int"}]


def _tc(tier, supersede, source_tag="ns1"):
    return TargetConfig(type="postgres", dsn_env=DSN_ENV,
        database="chunkshop_test_memory", table="mem",
        mode="create_if_missing", source_tag=source_tag,
        promote_metadata=PROMO, memory={"tier": tier, "supersede": supersede})


def _emb(n): return np.array([[0.1, 0.2, 0.3]] * n)


def test_consolidated_supersedes_provisional_scoped_by_source(ensure_pg):
    prov = load_sink(_tc("provisional", False), embed_dim=3)
    prov.create_table()
    prov.write_document("s1", [Chunk("s1", 0, "p", "p", {"session_id": "s1"})],
                        _emb(1), [[]])
    # a different namespace's row must survive supersede
    other = load_sink(_tc("provisional", False, source_tag="ns2"), embed_dim=3)
    other.write_document("s1", [Chunk("s1", 0, "o", "o", {"session_id": "s1"})],
                         _emb(1), [[]])
    cons = load_sink(_tc("consolidated", True), embed_dim=3)
    cons.write_document("s1", [Chunk("s1", 0, "c", "c",
                        {"session_id": "s1", "kind": "episode"})], _emb(1), [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute("SELECT tier, source FROM chunkshop_test_memory.mem ORDER BY source")
        rows = cur.fetchall()
    # ns1: only consolidated remains; ns2: provisional untouched
    assert ("consolidated", "ns1") in rows and ("provisional", "ns2") in rows
    assert ("provisional", "ns1") not in rows


def test_double_consolidate_is_idempotent(ensure_pg):
    cons = load_sink(_tc("consolidated", True), embed_dim=3)
    cons.create_table()
    ch = Chunk("s1", 0, "c", "c", {"session_id": "s1", "kind": "episode"})
    cons.write_document("s1", [ch], _emb(1), [[]])
    cons2 = load_sink(_tc("consolidated", True), embed_dim=3)
    cons2.write_document("s1", [ch], _emb(1), [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunkshop_test_memory.mem WHERE doc_id='s1'")
        assert cur.fetchone()[0] == 1


def test_soft_invalidate_retracts_older_contradicting_fact(ensure_pg):
    cons = load_sink(_tc("consolidated", True), embed_dim=3)
    cons.create_table()
    old = Chunk("s1", 1, "uses redis", "uses redis",
                {"session_id": "s1", "kind": "fact", "subject": "queue",
                 "predicate": "uses", "object": "redis",
                 "effective_from": "2026-01-01T00:00:00+00:00"})
    cons.write_document("s1", [old], _emb(1), [[]])
    new = Chunk("s2", 1, "uses postgres", "uses postgres",
                {"session_id": "s2", "kind": "fact", "subject": "queue",
                 "predicate": "uses", "object": "postgres",
                 "effective_from": "2026-03-01T00:00:00+00:00"})
    load_sink(_tc("consolidated", True), embed_dim=3).write_document(
        "s2", [new], _emb(1), [[]])
    with psycopg.connect(ensure_pg) as c, c.cursor() as cur:
        cur.execute("SELECT object, retracted FROM chunkshop_test_memory.mem "
                    "WHERE kind='fact' ORDER BY effective_from")
        rows = cur.fetchall()
    assert rows[0] == ("redis", True)      # older retracted
    assert rows[1] == ("postgres", False)  # newer live
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_sink_supersede.py -v`
Expected: FAIL initially if any column path missing — fix by ensuring Task 11 `_invalidate`/`_stamp` and the `PROMO` set cover `subject/predicate/object/effective_from/effective_to/retracted/retracted_at`. (If Task 11 was implemented exactly, `test_double_consolidate_is_idempotent` may already pass; the supersede/invalidate assertions drive any remaining fixes.)

- [ ] **Step 3: Fix implementation if needed**

If `test_soft_invalidate_*` fails because `effective_to`/`retracted_at` columns are absent: they are created by `create_if_missing` only from `promote_metadata`, which the test's `PROMO` includes — confirm Task 11's `_invalidate` UPDATE references exactly those promoted column names. No code change expected beyond Task 11; if the supersede deletes across namespaces, re-check the `DELETE ... AND source = %s` clause uses `self.cfg.source_tag`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_sink_supersede.py -v`
Expected: PASS (3 tests) or SKIP without Postgres.

- [ ] **Step 5: Commit**

```bash
git add python/tests/chunkshop/test_memory_sink_supersede.py python/src/chunkshop/sinks/memory_pg.py
git commit -m "test(memory): supersede scoping + idempotency + soft-invalidate"
```

---

### Task 13: `memory/` preset YAMLs

**Files:**
- Create: `python/src/chunkshop/configs/memory/realtime.yaml`
- Create: `python/src/chunkshop/configs/memory/consolidate.yaml`
- Test: `python/tests/chunkshop/test_memory_presets.py`

- [ ] **Step 1: Write the failing test**

```python
"""Both memory presets must load into a valid CellConfig."""
from pathlib import Path
import yaml
from chunkshop.config import CellConfig

BASE = Path(__file__).resolve().parents[2] / "src/chunkshop/configs/memory"


def test_realtime_preset_valid():
    c = CellConfig(**yaml.safe_load((BASE / "realtime.yaml").read_text()))
    assert c.source.type == "session_staging" and c.source.mode == "realtime"
    assert c.framer.type == "identity"
    assert c.target.memory.tier == "provisional"


def test_consolidate_preset_valid():
    c = CellConfig(**yaml.safe_load((BASE / "consolidate.yaml").read_text()))
    assert c.source.mode == "consolidate"
    assert c.framer.type == "session_episode"
    assert c.chunker.type == "consolidation"
    assert c.target.memory.tier == "consolidated"
    assert c.target.memory.supersede is True
    paths = {p.path for p in c.target.promote_metadata}
    assert {"kind", "session_id", "tier", "namespace", "subject", "predicate",
            "object", "support_span", "confidence", "effective_from",
            "effective_to", "retracted", "retracted_at", "recorded_at",
            "source_chunk_seq", "extractor"} <= paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_presets.py -v`
Expected: FAIL — preset files do not exist.

- [ ] **Step 3: Implement**

`python/src/chunkshop/configs/memory/realtime.yaml`:

```yaml
cell_name: memory_realtime
source:
  type: session_staging
  dsn: ${CHUNKSHOP_MEMORY_DSN}
  staging_table: chunkshop_staging
  staging_schema: public
  mode: realtime
framer:
  type: identity
chunker:
  type: fixed_overlap
  max_words: 200
  overlap_words: 20
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
target:
  type: postgres
  dsn: ${CHUNKSHOP_MEMORY_DSN}
  database: agent_memory
  table: memory
  mode: create_if_missing
  source_tag: default
  hnsw: true
  memory:
    tier: provisional
    supersede: false
  promote_metadata:
    - {path: kind, type: text}
    - {path: session_id, type: text}
    - {path: namespace, type: text}
    - {path: tier, type: text}
    - {path: recorded_at, type: timestamptz}
runtime:
  omp_num_threads: 4
  heartbeat_every: 25
```

`python/src/chunkshop/configs/memory/consolidate.yaml`:

```yaml
cell_name: memory_consolidate
source:
  type: session_staging
  dsn: ${CHUNKSHOP_MEMORY_DSN}
  staging_table: chunkshop_staging
  staging_schema: public
  mode: consolidate
  min_age_seconds: 3600
framer:
  type: session_episode
  max_gap_seconds: 1800
  max_turns: 40
  max_words: 1200
chunker:
  type: consolidation
  base:
    type: sentence_aware
    doc_type: prose
  consolidator:
    mode: callable
    module: chunkshop.consolidators.extractive
    function: consolidate
  fact_max_chars: 1200
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
target:
  type: postgres
  dsn: ${CHUNKSHOP_MEMORY_DSN}
  database: agent_memory
  table: memory
  mode: create_if_missing
  source_tag: default
  hnsw: true
  memory:
    tier: consolidated
    supersede: true
  promote_metadata:
    - {path: kind, type: text}
    - {path: session_id, type: text}
    - {path: namespace, type: text}
    - {path: tier, type: text}
    - {path: recorded_at, type: timestamptz}
    - {path: subject, type: text}
    - {path: predicate, type: text}
    - {path: object, type: text}
    - {path: support_span, type: text}
    - {path: confidence, type: text}
    - {path: effective_from, type: timestamptz}
    - {path: effective_to, type: timestamptz}
    - {path: retracted, type: boolean}
    - {path: retracted_at, type: timestamptz}
    - {path: source_chunk_seq, type: int}
    - {path: extractor, type: text}
runtime:
  omp_num_threads: 4
  heartbeat_every: 25
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_presets.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/configs/memory/ python/tests/chunkshop/test_memory_presets.py
git commit -m "feat(memory): realtime + consolidate preset YAMLs"
```

---

### Task 14: End-to-end + pg-raggraph contract test

**Files:**
- Create: `python/tests/fixtures/memory_session.jsonl`
- Create: `python/tests/chunkshop/test_memory_e2e.py`

- [ ] **Step 1: Write the failing test**

`python/tests/fixtures/memory_session.jsonl` (one JSON object per line):

```json
{"session_id": "s1", "seq": 1, "role": "user", "content": "We use Redis for the job queue.", "ts": "2026-01-01T10:00:00+00:00"}
{"session_id": "s1", "seq": 2, "role": "assistant", "content": "Understood, Redis backs the queue.", "ts": "2026-01-01T10:00:05+00:00"}
{"session_id": "s2", "seq": 1, "role": "user", "content": "We migrated the queue from Redis to Postgres.", "ts": "2026-03-01T09:00:00+00:00"}
{"session_id": "s2", "seq": 2, "role": "assistant", "content": "Confirmed, Postgres LISTEN/NOTIFY now backs the queue.", "ts": "2026-03-01T09:00:04+00:00"}
```

`python/tests/chunkshop/test_memory_e2e.py`:

```python
"""E2E: stage → realtime cell → consolidate cell; assert store + pg-raggraph contract."""
import json, os, psycopg, pytest
from pathlib import Path
DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"

@pytest.fixture
def ensure_pg():
    dsn = os.environ.get(DSN_ENV, DEFAULT_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except Exception as exc:
        pytest.skip(f"PG at {dsn} not reachable: {exc}")
    os.environ[DSN_ENV] = dsn
    os.environ["CHUNKSHOP_MEMORY_DSN"] = dsn
    yield dsn
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS public.chunkshop_staging CASCADE")
        cur.execute("DROP SCHEMA IF EXISTS agent_memory CASCADE")
        conn.commit()

from chunkshop.memory import ensure_staging_table, stage_events
from chunkshop.config import load_config
from chunkshop.runner import run_cell

PRESETS = Path(__file__).resolve().parents[2] / "src/chunkshop/configs/memory"
FIX = Path(__file__).resolve().parent.parent / "fixtures/memory_session.jsonl"

# pg-raggraph facts contract: columns SP-B reads with zero shim
PGRG_FACT_COLS = {"subject", "predicate", "object", "support_span", "confidence",
                  "effective_from", "effective_to", "retracted", "retracted_at",
                  "extractor", "namespace"}


def _run(name):
    return run_cell(load_config(str(PRESETS / name)))


def test_e2e_realtime_then_consolidate(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, table="chunkshop_staging", schema="public")
    events = [json.loads(l) for l in FIX.read_text().splitlines() if l.strip()]
    stage_events(dsn, events, table="chunkshop_staging", schema="public")

    r1 = _run("realtime.yaml")
    assert r1.error is None and r1.chunks_written > 0
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_memory.memory WHERE tier='provisional'")
        assert cur.fetchone()[0] > 0

    # consolidate ignores min_age in this fixture by setting it to 0 at runtime
    cfg = load_config(str(PRESETS / "consolidate.yaml"))
    cfg.source.min_age_seconds = 0
    r2 = run_cell(cfg)
    assert r2.error is None and r2.chunks_written > 0

    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_memory.memory WHERE tier='provisional'")
        assert cur.fetchone()[0] == 0          # superseded
        cur.execute("SELECT count(*) FROM agent_memory.memory WHERE kind='episode'")
        assert cur.fetchone()[0] >= 1
        cur.execute("SELECT count(*) FROM agent_memory.memory WHERE kind='fact'")
        assert cur.fetchone()[0] >= 1


def test_pgraggraph_contract_columns_present(ensure_pg):
    dsn = ensure_pg
    ensure_staging_table(dsn, table="chunkshop_staging", schema="public")
    stage_events(dsn, [{"session_id": "s1", "seq": 1, "role": "user",
                        "content": "hello world this is a sentence."}],
                 table="chunkshop_staging", schema="public")
    cfg = load_config(str(PRESETS / "consolidate.yaml"))
    cfg.source.min_age_seconds = 0
    run_cell(cfg)
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='agent_memory' AND table_name='memory'")
        cols = {r[0] for r in cur.fetchall()}
    missing = PGRG_FACT_COLS - cols
    assert not missing, f"pg-raggraph contract drift: missing {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_e2e.py -v`
Expected: FAIL on first run if any wiring gap remains (e.g. `extractor`/`object` not promoted, `min_age` filtering). Fixture + presets from prior tasks should make it pass after wiring fixes.

- [ ] **Step 3: Fix wiring**

Likely fixes surfaced here: (a) ensure `consolidate.yaml` `promote_metadata` includes every `PGRG_FACT_COLS` entry (it does per Task 13 — if `test_pgraggraph_contract` fails, reconcile names); (b) the extractive consolidator stamps `extractor` — add `m.setdefault("extractor", self._mode)` is NOT done; instead the `ConsolidationChunker` already stamps `consolidator`; add `extractor` alias: in `ConsolidationChunker.chunk`, add `"extractor": self._mode` to both `ep_meta` and `fm`. Make that one-line change and re-commit Task 10 file. (c) If `run_cell` aborts on the empty-fact session, confirm O4 passthrough path returns a valid episode chunk.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python && uv run pytest tests/chunkshop/test_memory_e2e.py -v`
Expected: PASS (2 tests) or SKIP without Postgres.

- [ ] **Step 5: Commit**

```bash
git add python/tests/fixtures/memory_session.jsonl python/tests/chunkshop/test_memory_e2e.py python/src/chunkshop/chunkers/consolidation.py
git commit -m "test(memory): e2e realtime→consolidate + pg-raggraph contract guard"
```

---

### Task 15: Regression sweep + docs note

**Files:**
- Modify: `python/CLAUDE.md` or `docs/` (a short "Agent memory (SP-A)" usage note — append to existing docs index, do not create a new top-level doc)
- Test: full suite

- [ ] **Step 1: Run the full suite**

Run: `cd python && uv run pytest -q`
Expected: all prior tests still pass; new memory tests pass or skip (no Postgres). No collection errors. If any pre-existing test broke, it indicates a config-union or loader regression — fix before proceeding.

- [ ] **Step 2: Add a usage note**

Append to `docs/incremental.md` (the existing patterns doc) a short section: how to `chunkshop.memory.stage_event(...)`, then schedule `chunkshop ingest --config src/chunkshop/configs/memory/realtime.yaml` frequently and `consolidate.yaml` nightly via external cron; note `CHUNKSHOP_MEMORY_DSN`; link the spec. Keep to ~15 lines, no new file.

- [ ] **Step 3: Commit**

```bash
git add python/ docs/
git commit -m "docs(memory): SP-A usage note + regression sweep green"
```

---

## Self-Review

**1. Spec coverage:**
- D1–D9, SC-1 (Task 5), SC-2 (Tasks 1–4,6,7,10,11), SC-3 (Tasks 8,9), SC-4 (Tasks 12,14), SC-5 (Task 14 contract test), SC-6 (O1 Task14 implicit via rebuild/min_age; O3 per-session commit inherited; O4 Task 10; O7 Task 12 scoping; O8 Task 12 ordering), SC-7 (Task 15). O1 explicit "late-event rebuild" and O3 "crash mid-run" lack a dedicated test — **gap accepted for v1**: delete-then-insert idempotency (Task 12 `test_double_consolidate_is_idempotent`) covers the mechanism O1 relies on; a dedicated late-event-rebuild test is a fast-follow, noted here rather than silently dropped.
- Embedding-based topic-shift: spec §9 marks it out of scope — no task, correct.
- `query_top_k` retrieval smoke: spec §7 lists it; not separately tested (inherited unchanged from `PgSink`) — acceptable, no behavior change.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step has complete code. Task 12 Step 3 is a conditional-fix step (legitimate in TDD when a prior task may already satisfy it) and names the exact clause to check.

**3. Type consistency:** `build_consolidator` returns `{"summary","facts":[{subject,predicate,object,support_span,confidence}]}` — produced by `_normalize` (Task 8), consumed by `ConsolidationChunker` (Task 10), asserted in Tasks 10/12/14. `kind`/`tier`/`namespace`/`recorded_at`/`effective_from`/`subject`/`predicate`/`object`/`support_span`/`retracted`/`retracted_at`/`effective_to`/`source_chunk_seq`/`extractor` names are identical across chunker stamping (Task 10), sink stamping/invalidate (Task 11/12), presets (Task 13), contract test (Task 14). `MemorySink` ctor signature matches `PgSink` (`cfg, backend, embed_dim`) and `load_sink` dispatch. `_documents_from_rows` tuple order matches the `_SELECT` column order (Task 6).

Fix applied inline during review: Task 14 Step 3 adds the `extractor` stamp to `ConsolidationChunker` (Task 10 emitted `consolidator` only; the pg-raggraph contract needs `extractor`) — recorded as an explicit Task 14 fix + re-commit of the Task 10 file so the type/column set stays consistent.
