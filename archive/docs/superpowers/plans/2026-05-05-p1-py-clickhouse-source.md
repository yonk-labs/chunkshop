# P1 — Python ClickHouse Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `python/src/chunkshop/sources/clickhouse_table.py` so chunkshop reads source documents from ClickHouse the same way it does from Postgres, MariaDB, and SQLite. Closes OQ4 from the v4 modular-backends design and unblocks the 16-cell cross-backend matrix.

**Architecture:** New `ClickhouseTableSource` class implementing the `Source` Protocol (`iter_documents() -> Iterator[Document]`). Structurally a copy of `mariadb_table.py` with three substitutions: (1) no cursor — `ClickHouseBackend.connect()` yields the `clickhouse-connect` Client directly; (2) streaming via `client.query_rows_stream(sql)` returning a `StreamContext`; (3) recursive `_json_safe` handling CH's broader scalar set (`UUID`, `IPv4Address`, `IPv6Address`, `Decimal`) plus nested `list`/`tuple`/`dict`. Trusted-`where` and identifier-safety contracts mirror siblings exactly.

**Tech Stack:** Python 3.12, pydantic 2, `clickhouse-connect>=0.7`, pytest. Existing `ClickHouseBackend` (`backends/clickhouse.py`) and CH 24.10 docker container (`docker-compose.test.yaml`). Test DSN env var: `CHUNKSHOP_TEST_DSN_CH`.

**Spec:** [`docs/superpowers/specs/2026-05-05-p1-py-clickhouse-source-design.md`](../specs/2026-05-05-p1-py-clickhouse-source-design.md)

---

## Working directory

All commands assume `cwd = python/` unless noted. Commit messages use the project's existing style (lowercase scope prefix, e.g. `feat(source):`, `test(source):`, `chore(deps):`).

## Pre-flight check

- [ ] **Step 0.1: Confirm worktree + branch + clean tree**

Run:
```bash
git -C /home/yonk/yonk-tools/chunkshop-py-ch-source status --short
git -C /home/yonk/yonk-tools/chunkshop-py-ch-source branch --show-current
```
Expected: clean tree on `experimental/v4-py-clickhouse-source`.

- [ ] **Step 0.2: Confirm CH container is up**

Run:
```bash
docker compose -f /home/yonk/yonk-tools/chunkshop-py-ch-source/docker-compose.test.yaml ps clickhouse
```
Expected: container in `Up` state. If not: `docker compose -f /home/yonk/yonk-tools/chunkshop-py-ch-source/docker-compose.test.yaml up -d clickhouse`

- [ ] **Step 0.3: Export CH DSN for the rest of the session**

Run:
```bash
export CHUNKSHOP_TEST_DSN_CH=clickhouse://default:chpw@localhost:8124/default
```

---

## Task 1: Add `[clickhouse]` extra to `pyproject.toml`

The v4 modular-backends spec called for this extra; the prior CH-sink work (which landed `backends/clickhouse.py`, `sinks/clickhouse.py`, and the existing CH tests) never actually added it to `pyproject.toml`. P1 is the natural place to fix this — without it, `uv sync` won't pull `clickhouse-connect` and the new source can't run.

**Files:**
- Modify: `python/pyproject.toml`

- [ ] **Step 1.1: Add the extra**

Edit `python/pyproject.toml`. In the `[project.optional-dependencies]` table, add a `clickhouse` entry and update `all-backends` to include it.

Before:
```toml
sqlite = ["sqlite-vec>=0.1.6"]
mariadb = ["PyMySQL>=1.1"]
all-backends = ["chunkshop[sqlite,mariadb]"]
```

After:
```toml
sqlite = ["sqlite-vec>=0.1.6"]
mariadb = ["PyMySQL>=1.1"]
clickhouse = ["clickhouse-connect>=0.7"]
all-backends = ["chunkshop[sqlite,mariadb,clickhouse]"]
```

- [ ] **Step 1.2: Re-sync the dev environment**

Run:
```bash
uv sync --extra dev --extra extractors --extra clickhouse --extra mariadb --extra sqlite
```
Expected: `Installed N packages` line, including `clickhouse-connect` if it wasn't already present.

- [ ] **Step 1.3: Verify the existing CH backend test still passes**

Run:
```bash
uv run pytest tests/chunkshop/test_backend_clickhouse.py -v
```
Expected: tests pass (or all skip if `CHUNKSHOP_TEST_DSN_CH` is unset). No errors importing `clickhouse_connect`.

- [ ] **Step 1.4: Commit**

```bash
git add python/pyproject.toml python/uv.lock
git commit -m "chore(deps): add [clickhouse] extra so CH backend installs cleanly

Brings pyproject.toml into line with the v4 modular-backends design,
which named clickhouse-connect>=0.7 as the optional dependency for
ClickHouse support but was never wired into [project.optional-dependencies].
all-backends now pulls all three optional storage backends.

Prerequisite for P1 (Python ClickHouse source); also retroactively
covers the existing CH sink/backend code."
```

---

## Task 2: Add `ClickhouseTableSource` config model

TDD shape: write a config-parse test first (it will fail with `ValueError` because the `Union` doesn't include the new type), then add the model.

**Files:**
- Modify: `python/src/chunkshop/config.py` — add new pydantic model + extend `SourceConfig` union
- Test: `python/tests/chunkshop/test_config_clickhouse_source.py` (new)

- [ ] **Step 2.1: Write the failing config test**

Create `python/tests/chunkshop/test_config_clickhouse_source.py`:

```python
"""Config-load tests for ClickhouseTableSource (P1-SC-001)."""
import pytest
from pydantic import ValidationError

from chunkshop.config import ClickhouseTableSource


def test_minimum_valid_config_parses():
    cfg = ClickhouseTableSource(
        type="clickhouse_table",
        dsn_env="CHUNKSHOP_TEST_DSN_CH",
        database="my_app",
        table="documents",
        id_column="id",
        content_column="body",
    )
    assert cfg.database_name == "my_app"   # alias=database
    assert cfg.title_column is None
    assert cfg.where is None
    assert cfg.metadata_columns == []


def test_full_config_parses():
    cfg = ClickhouseTableSource(
        type="clickhouse_table",
        dsn_env="CHUNKSHOP_TEST_DSN_CH",
        database="my_app",
        table="documents",
        id_column="id",
        content_column="body",
        title_column="headline",
        where="created_at > toDateTime('2025-01-01 00:00:00')",
        metadata_columns=["lang", "author"],
    )
    assert cfg.title_column == "headline"
    assert cfg.where.startswith("created_at >")
    assert cfg.metadata_columns == ["lang", "author"]


def test_typo_rejected_extra_forbid():
    with pytest.raises(ValidationError) as ei:
        ClickhouseTableSource(
            type="clickhouse_table",
            dsn_env="X", database="d", table="t",
            id_column="id", content_column="body",
            metadata_colmns=["x"],   # typo
        )
    assert "metadata_colmns" in str(ei.value)


def test_wrong_type_rejected():
    with pytest.raises(ValidationError):
        ClickhouseTableSource(
            type="not_a_real_type",
            dsn_env="X", database="d", table="t",
            id_column="id", content_column="body",
        )


def test_load_source_dispatches_clickhouse_table():
    from chunkshop.sources import load_source
    cfg = ClickhouseTableSource(
        type="clickhouse_table",
        dsn_env="CHUNKSHOP_TEST_DSN_CH",
        database="my_app", table="documents",
        id_column="id", content_column="body",
    )
    src = load_source(cfg)
    assert type(src).__name__ == "ClickhouseTableSource"
```

- [ ] **Step 2.2: Run the test — expect ImportError**

Run:
```bash
uv run pytest tests/chunkshop/test_config_clickhouse_source.py -v
```
Expected: collection error — `ImportError: cannot import name 'ClickhouseTableSource' from 'chunkshop.config'`.

- [ ] **Step 2.3: Add the pydantic model**

Edit `python/src/chunkshop/config.py`. Locate the `MariaDbTableSource` class (around line 60). Immediately after its closing line (around line 69), insert:

```python
class ClickhouseTableSource(_Base):
    type: Literal["clickhouse_table"]
    dsn_env: str
    database_name: str = Field(alias="database")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    # `where` is documented as TRUSTED OPERATOR INPUT — raw passthrough into
    # SQL, no parameterization. Same contract as PgTableSource /
    # MariaDbTableSource / SqliteTableSource. CH SQL dialect example:
    #   where: "created_at > toDateTime('2025-01-01 00:00:00')"
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)
```

Then update the `SourceConfig` union (around line 100). Before:
```python
SourceConfig = Annotated[
    Union[FilesSource, JsonCorpusSource, PgTableSource, SqliteTableSource,
          MariaDbTableSource, HttpSource, S3Source, InlineSource],
    Field(discriminator="type"),
]
```

After:
```python
SourceConfig = Annotated[
    Union[FilesSource, JsonCorpusSource, PgTableSource, SqliteTableSource,
          MariaDbTableSource, ClickhouseTableSource, HttpSource, S3Source, InlineSource],
    Field(discriminator="type"),
]
```

- [ ] **Step 2.4: Run the config test — most pass, the dispatch test still fails**

Run:
```bash
uv run pytest tests/chunkshop/test_config_clickhouse_source.py -v
```
Expected: 4 of 5 pass. `test_load_source_dispatches_clickhouse_table` fails because `load_source()` has no branch yet.

- [ ] **Step 2.5: Add the `load_source` branch**

Edit `python/src/chunkshop/sources/__init__.py`.

In the imports block, add (alphabetical-ish — group with the other DB cfgs):
```python
from chunkshop.config import (
    FilesSource as FilesCfg,
    InlineSource as InlineCfg,
    JsonCorpusSource as JsonCfg,
    PgTableSource as PgCfg,
    SqliteTableSource as SqliteCfg,
    MariaDbTableSource as MariaDbCfg,
    ClickhouseTableSource as ChCfg,
    HttpSource as HttpCfg,
    S3Source as S3Cfg,
    SourceConfig,
)
```

In the file-impl imports, add:
```python
from chunkshop.sources.clickhouse_table import ClickhouseTableSource
```

In `load_source()`, after the `MariaDbCfg` branch and before the `HttpCfg` branch, add:
```python
    if isinstance(cfg, ChCfg):
        return ClickhouseTableSource(cfg)
```

This will fail at import time because `clickhouse_table.py` doesn't exist yet — that's expected; we'll create it as a stub in the next step.

- [ ] **Step 2.6: Create stub `clickhouse_table.py`**

Create `python/src/chunkshop/sources/clickhouse_table.py` with just enough to make the dispatch test pass:

```python
"""ClickHouse table source — see docs/superpowers/specs/2026-05-05-p1-py-clickhouse-source-design.md."""
from __future__ import annotations
from typing import Iterator

from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.config import ClickhouseTableSource as Cfg
from chunkshop.sources.base import Document


class ClickhouseTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = ClickHouseBackend(dsn_env=cfg.dsn_env)

    def iter_documents(self) -> Iterator[Document]:
        raise NotImplementedError("filled in by Task 3")
```

- [ ] **Step 2.7: Run the config tests — all pass**

Run:
```bash
uv run pytest tests/chunkshop/test_config_clickhouse_source.py -v
```
Expected: 5 passed.

- [ ] **Step 2.8: Run the full test suite — no regressions**

Run:
```bash
uv run pytest -q
```
Expected: prior pass count + 5 new passes. No new failures.

- [ ] **Step 2.9: Commit**

```bash
git add python/src/chunkshop/config.py \
        python/src/chunkshop/sources/__init__.py \
        python/src/chunkshop/sources/clickhouse_table.py \
        python/tests/chunkshop/test_config_clickhouse_source.py
git commit -m "feat(source): add ClickhouseTableSource config model + load_source dispatch

Pydantic discriminated-union entry for type=clickhouse_table. Fields
mirror MariaDbTableSource exactly; \`where\` is documented as trusted
operator input. extra=\"forbid\" inherited from _Base catches typos at
config-load time.

Source impl is a stub for now (raises NotImplementedError on
iter_documents); the streaming impl lands in the next commit.

P1-SC-001, P1-SC-002. Spec: docs/superpowers/specs/2026-05-05-p1-py-clickhouse-source-design.md"
```

---

## Task 3: Implement `iter_documents` happy path (P1-T1)

**Files:**
- Modify: `python/src/chunkshop/sources/clickhouse_table.py` — replace stub
- Test: `python/tests/chunkshop/test_source_clickhouse.py` (new)

- [ ] **Step 3.1: Write the failing happy-path test**

Create `python/tests/chunkshop/test_source_clickhouse.py`:

```python
"""ClickhouseTableSource integration tests (P1-T1..T5).

Each test creates and drops its own database to avoid cross-test pollution.
All tests skipped if CHUNKSHOP_TEST_DSN_CH is unset.
"""
import os
import pytest

pytest.importorskip("clickhouse_connect")

from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.config import ClickhouseTableSource as Cfg
from chunkshop.sources.clickhouse_table import ClickhouseTableSource as Source

DSN_VAR = "CHUNKSHOP_TEST_DSN_CH"
DSN = os.environ.get(DSN_VAR)
pytestmark = pytest.mark.skipif(not DSN, reason=f"{DSN_VAR} not set")


def _drop_db(client, db: str) -> None:
    client.command(f"DROP DATABASE IF EXISTS `{db}` SYNC")


def _create_db(client, db: str) -> None:
    _drop_db(client, db)
    client.command(f"CREATE DATABASE `{db}`")


def test_p1_t1_iter_documents_happy_path():
    """P1-T1: 2 documents with metadata_columns round-trip cleanly."""
    db = "chunkshop_src_test_t1"
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            client.command(
                f"CREATE TABLE `{db}`.`docs` "
                f"(id String, body String, lang String) "
                f"ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                f"`{db}`.`docs`",
                [["a", "first body", "en"], ["b", "second body", "fr"]],
                column_names=["id", "body", "lang"],
            )

        cfg = Cfg(
            type="clickhouse_table",
            dsn_env=DSN_VAR,
            database=db,
            table="docs",
            id_column="id",
            content_column="body",
            metadata_columns=["lang"],
        )
        src = Source(cfg)
        docs = list(src.iter_documents())
        assert len(docs) == 2
        by_id = {d.id: d for d in docs}
        assert by_id["a"].content == "first body"
        assert by_id["a"].metadata == {"lang": "en"}
        assert by_id["b"].metadata == {"lang": "fr"}
        assert by_id["a"].title is None
    finally:
        with be.connect() as client:
            _drop_db(client, db)
```

- [ ] **Step 3.2: Run the test — expect NotImplementedError**

Run:
```bash
uv run pytest tests/chunkshop/test_source_clickhouse.py::test_p1_t1_iter_documents_happy_path -v
```
Expected: FAIL with `NotImplementedError: filled in by Task 3`.

- [ ] **Step 3.3: Implement `iter_documents`**

Replace `python/src/chunkshop/sources/clickhouse_table.py` with:

```python
"""ClickHouse table source — see docs/superpowers/specs/2026-05-05-p1-py-clickhouse-source-design.md.

Three differences from sibling sources (pg_table.py, mariadb_table.py,
sqlite_table.py):

1. No cursor — ClickHouseBackend.connect() yields the clickhouse-connect
   Client directly (see backends/clickhouse.py:6-12).
2. Streaming iteration — uses client.query_rows_stream(sql) which
   returns a StreamContext (context manager). Diverges from PG/MariaDB
   siblings which fully buffer in RAM. Justified by CH's typical scale.
3. Recursive _json_safe — handles CH's broader scalar set (UUID,
   IPv4Address, IPv6Address, Decimal) plus nested list/tuple/dict.
"""
from __future__ import annotations
import base64
import datetime
import decimal
import ipaddress
import uuid
from typing import Any, Iterator

from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.config import ClickhouseTableSource as Cfg
from chunkshop.sources.base import Document


def _json_safe(v: Any) -> Any:
    """Coerce clickhouse-connect-returned values to JSON-serializable forms.

    Recurses into list/tuple/dict because CH's Tuple/Array/Map types
    nest more deeply than PG/MariaDB row scalars in practice. Tuples
    become lists (true JSON has no tuple).
    """
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return str(v)
    if isinstance(v, bytes):
        return base64.b64encode(v).decode("ascii")
    if isinstance(v, dict):
        return {k: _json_safe(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


class ClickhouseTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = ClickHouseBackend(dsn_env=cfg.dsn_env)

    def iter_documents(self) -> Iterator[Document]:
        cols = [self.cfg.id_column, self.cfg.content_column]
        title_idx = None
        if self.cfg.title_column:
            title_idx = len(cols)
            cols.append(self.cfg.title_column)
        meta_start = len(cols)
        cols.extend(self.cfg.metadata_columns)

        cols_sql = ", ".join(self.backend.quote_ident(c) for c in cols)
        fq = self.backend.fq_table(self.cfg.database_name, self.cfg.table)
        query = f"SELECT {cols_sql} FROM {fq}"
        if self.cfg.where:
            # `where` is documented as TRUSTED OPERATOR INPUT — same contract
            # as PgTableSource / MariaDbTableSource / SqliteTableSource.
            query += f" WHERE {self.cfg.where}"

        with self.backend.connect() as client:
            with client.query_rows_stream(query) as stream:
                for row in stream:
                    metadata = {
                        self.cfg.metadata_columns[i]: _json_safe(row[meta_start + i])
                        for i in range(len(self.cfg.metadata_columns))
                    }
                    yield Document(
                        id=str(row[0]),
                        content=row[1],
                        title=row[title_idx] if title_idx is not None else None,
                        metadata=metadata if metadata else None,
                    )
```

- [ ] **Step 3.4: Run the happy-path test — passes**

Run:
```bash
uv run pytest tests/chunkshop/test_source_clickhouse.py::test_p1_t1_iter_documents_happy_path -v
```
Expected: PASS.

- [ ] **Step 3.5: Commit**

```bash
git add python/src/chunkshop/sources/clickhouse_table.py \
        python/tests/chunkshop/test_source_clickhouse.py
git commit -m "feat(source): implement ClickhouseTableSource.iter_documents

Streams rows via clickhouse-connect's query_rows_stream (StreamContext
context manager) — diverges from PG/MariaDB siblings which fully
buffer. Justified in the spec: CH's typical scale (millions of rows
per source table) makes buffering an OOM foot-gun where the same
pattern on PG/MariaDB is rare.

Recursive _json_safe handles CH's broader scalar set (UUID,
IPv4/IPv6, Decimal) plus nested list/tuple/dict.

Trusted-where contract carries the same docstring comment as
mariadb_table.py:40.

P1-T1 (happy path) green. P1-SC-003 covered."
```

---

## Task 4: Recursive `_json_safe` test (P1-T3)

**Files:**
- Test: `python/tests/chunkshop/test_source_clickhouse.py` — append test

- [ ] **Step 4.1: Add the recursive coercion test**

Append to `python/tests/chunkshop/test_source_clickhouse.py`:

```python
def test_p1_t3_json_safe_recursive_coercion():
    """P1-T3: nested CH types coerce to JSON-serializable forms.

    Covers Array(DateTime), Map(String, UUID), Decimal, Tuple, IPv4,
    Nullable. Asserts json.dumps(metadata) succeeds.
    """
    import json

    db = "chunkshop_src_test_t3"
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            # CH requires allow_suspicious_low_cardinality_types and similar
            # flags for some experimental types but Array/Map/Tuple/Nullable
            # are all stable in 24.10.
            client.command(
                f"CREATE TABLE `{db}`.`docs` ("
                f"id String, body String, "
                f"  ts_array Array(DateTime), "
                f"  uuid_map Map(String, UUID), "
                f"  amount Decimal(10, 2), "
                f"  tup Tuple(String, Date), "
                f"  ip Nullable(IPv4), "
                f"  ip_null Nullable(IPv4)"
                f") ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                f"`{db}`.`docs`",
                [[
                    "doc1", "body text",
                    [datetime.datetime(2025, 1, 1, 12, 0, 0),
                     datetime.datetime(2025, 6, 15, 9, 30, 0)],
                    {"a": uuid.UUID("12345678-1234-5678-1234-567812345678")},
                    decimal.Decimal("123.45"),
                    ("hello", datetime.date(2025, 3, 1)),
                    ipaddress.IPv4Address("192.168.1.1"),
                    None,
                ]],
                column_names=["id", "body", "ts_array", "uuid_map", "amount",
                              "tup", "ip", "ip_null"],
            )

        cfg = Cfg(
            type="clickhouse_table",
            dsn_env=DSN_VAR,
            database=db,
            table="docs",
            id_column="id",
            content_column="body",
            metadata_columns=["ts_array", "uuid_map", "amount", "tup", "ip", "ip_null"],
        )
        docs = list(Source(cfg).iter_documents())
        assert len(docs) == 1
        meta = docs[0].metadata

        # Must be JSON-serializable end-to-end (this is the round-trip
        # the sink will perform via json.dumps).
        serialized = json.dumps(meta)
        assert serialized   # truthy = succeeded

        # Spot checks on the coerced shapes
        assert isinstance(meta["ts_array"], list)
        assert all(isinstance(x, str) for x in meta["ts_array"])
        assert "2025-01-01" in meta["ts_array"][0]

        assert isinstance(meta["uuid_map"], dict)
        assert meta["uuid_map"]["a"] == "12345678-1234-5678-1234-567812345678"

        assert meta["amount"] == 123.45
        assert isinstance(meta["amount"], float)

        # Tuple → list
        assert isinstance(meta["tup"], list)
        assert meta["tup"][0] == "hello"
        assert meta["tup"][1] == "2025-03-01"

        assert meta["ip"] == "192.168.1.1"
        assert meta["ip_null"] is None
    finally:
        with be.connect() as client:
            _drop_db(client, db)
```

Add the imports at the top of the test file (next to `import os`):
```python
import datetime
import decimal
import ipaddress
import uuid
```

- [ ] **Step 4.2: Run the test**

Run:
```bash
uv run pytest tests/chunkshop/test_source_clickhouse.py::test_p1_t3_json_safe_recursive_coercion -v
```
Expected: PASS. If `Map(String, UUID)` insert fails because clickhouse-connect's binding doesn't accept a Python dict → check the driver's docs page; the workaround if needed is to insert via a literal `INSERT ... VALUES` SQL using `mapFromArrays(...)` — flag in commit if changed.

- [ ] **Step 4.3: Commit**

```bash
git add python/tests/chunkshop/test_source_clickhouse.py
git commit -m "test(source): cover recursive _json_safe for CH nested types

Single-row table exercises Array(DateTime), Map(String, UUID),
Decimal, Tuple, Nullable(IPv4), and a NULL Nullable(IPv4). End-to-end
assertion is json.dumps(metadata) — this is the round-trip the sink
performs.

P1-T3, P1-SC-004."
```

---

## Task 5: `title_column` optional behavior (P1-T5)

**Files:**
- Test: `python/tests/chunkshop/test_source_clickhouse.py` — append test

- [ ] **Step 5.1: Add the test**

Append to `python/tests/chunkshop/test_source_clickhouse.py`:

```python
def test_p1_t5_title_column_optional():
    """P1-T5: title_column is None → Document.title is None;
    title_column='headline' → Document.title == row.headline."""
    db = "chunkshop_src_test_t5"
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            client.command(
                f"CREATE TABLE `{db}`.`docs` "
                f"(id String, body String, headline String) "
                f"ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                f"`{db}`.`docs`",
                [["a", "body-a", "Hello A"], ["b", "body-b", "Hello B"]],
                column_names=["id", "body", "headline"],
            )

        # Without title_column
        cfg_no_title = Cfg(
            type="clickhouse_table", dsn_env=DSN_VAR,
            database=db, table="docs",
            id_column="id", content_column="body",
        )
        docs = list(Source(cfg_no_title).iter_documents())
        assert all(d.title is None for d in docs)

        # With title_column
        cfg_with_title = Cfg(
            type="clickhouse_table", dsn_env=DSN_VAR,
            database=db, table="docs",
            id_column="id", content_column="body",
            title_column="headline",
        )
        docs = list(Source(cfg_with_title).iter_documents())
        by_id = {d.id: d for d in docs}
        assert by_id["a"].title == "Hello A"
        assert by_id["b"].title == "Hello B"
    finally:
        with be.connect() as client:
            _drop_db(client, db)
```

- [ ] **Step 5.2: Run the test**

Run:
```bash
uv run pytest tests/chunkshop/test_source_clickhouse.py::test_p1_t5_title_column_optional -v
```
Expected: PASS.

- [ ] **Step 5.3: Commit**

```bash
git add python/tests/chunkshop/test_source_clickhouse.py
git commit -m "test(source): cover title_column optional path

Verifies the conditional column-list build in iter_documents: without
title_column, docs[*].title is None; with title_column='headline',
docs[*].title carries the row value.

P1-T5, P1-SC-003."
```

---

## Task 6: Trusted-`where` contract (P1-T4)

**Files:**
- Test: `python/tests/chunkshop/test_source_clickhouse.py` — append test

- [ ] **Step 6.1: Add the test**

Append to `python/tests/chunkshop/test_source_clickhouse.py`:

```python
def test_p1_t4_where_clause_trusted_input():
    """P1-T4: cfg.where is interpolated raw into SQL with CH dialect.
    Operator-trusted contract — same as PG/MariaDB siblings."""
    db = "chunkshop_src_test_t4"
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            client.command(
                f"CREATE TABLE `{db}`.`docs` "
                f"(id String, body String, created_at DateTime) "
                f"ENGINE = MergeTree() ORDER BY id"
            )
            client.insert(
                f"`{db}`.`docs`",
                [
                    ["old", "old body", datetime.datetime(2024, 1, 1, 0, 0, 0)],
                    ["new1", "new body 1", datetime.datetime(2025, 7, 1, 0, 0, 0)],
                    ["new2", "new body 2", datetime.datetime(2025, 8, 1, 0, 0, 0)],
                ],
                column_names=["id", "body", "created_at"],
            )

        cfg = Cfg(
            type="clickhouse_table", dsn_env=DSN_VAR,
            database=db, table="docs",
            id_column="id", content_column="body",
            where="created_at > toDateTime('2025-06-01 00:00:00')",
        )
        docs = list(Source(cfg).iter_documents())
        ids = sorted(d.id for d in docs)
        assert ids == ["new1", "new2"]
    finally:
        with be.connect() as client:
            _drop_db(client, db)
```

- [ ] **Step 6.2: Run the test**

Run:
```bash
uv run pytest tests/chunkshop/test_source_clickhouse.py::test_p1_t4_where_clause_trusted_input -v
```
Expected: PASS.

- [ ] **Step 6.3: Commit**

```bash
git add python/tests/chunkshop/test_source_clickhouse.py
git commit -m "test(source): cover trusted-where contract with CH dialect

Uses toDateTime() predicate — CH-flavored. Verifies cfg.where is
interpolated raw, same operator-trust contract as the three sibling
DB sources.

P1-T4, P1-SC-006."
```

---

## Task 7: Streaming smoke test (P1-T2)

This is the soft test discussed in the spec. Locks in `query_rows_stream` as the call shape — a regression to `query` would either deadlock the wall-clock check or skip the stream-context-manager codepath.

**Files:**
- Test: `python/tests/chunkshop/test_source_clickhouse.py` — append test

- [ ] **Step 7.1: Add the test**

Append to `python/tests/chunkshop/test_source_clickhouse.py`:

```python
def test_p1_t2_streaming_does_not_materialize():
    """P1-T2: streaming iteration is wired (uses query_rows_stream).

    Soft test — locks in the call shape. Seeds 2k rows, iterates fully,
    asserts:
      1. iteration completes (rows are reachable)
      2. the count matches what was inserted
      3. cleanup-on-early-exit doesn't blow up

    A regression that switches query_rows_stream → query would still
    pass (1) and (2) — this test is primarily a code-review marker
    that the streaming code path exists.
    """
    db = "chunkshop_src_test_t2"
    n_rows = 2_000
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    try:
        with be.connect() as client:
            _create_db(client, db)
            client.command(
                f"CREATE TABLE `{db}`.`docs` "
                f"(id String, body String) "
                f"ENGINE = MergeTree() ORDER BY id"
            )
            rows = [[f"doc{i:05d}", f"body of document {i}"] for i in range(n_rows)]
            client.insert(f"`{db}`.`docs`", rows, column_names=["id", "body"])

        cfg = Cfg(
            type="clickhouse_table", dsn_env=DSN_VAR,
            database=db, table="docs",
            id_column="id", content_column="body",
        )
        src = Source(cfg)

        # Full iteration: count must match.
        all_docs = list(src.iter_documents())
        assert len(all_docs) == n_rows

        # Early-exit cleanup: take a few then bail. The StreamContext's
        # __exit__ should release the chunked HTTP response cleanly.
        partial = []
        for d in src.iter_documents():
            partial.append(d)
            if len(partial) >= 5:
                break
        assert len(partial) == 5
    finally:
        with be.connect() as client:
            _drop_db(client, db)
```

- [ ] **Step 7.2: Run the test**

Run:
```bash
uv run pytest tests/chunkshop/test_source_clickhouse.py::test_p1_t2_streaming_does_not_materialize -v
```
Expected: PASS. Insert of 2k rows takes a few seconds; full iteration takes a few more.

- [ ] **Step 7.3: Run the full source test file**

Run:
```bash
uv run pytest tests/chunkshop/test_source_clickhouse.py -v
```
Expected: 5 passed (T1, T2, T3, T4, T5).

- [ ] **Step 7.4: Commit**

```bash
git add python/tests/chunkshop/test_source_clickhouse.py
git commit -m "test(source): smoke-test streaming iteration over 2k rows

Verifies query_rows_stream is wired and that early-exit (break out
of iteration) doesn't blow up cleanup. Soft test — primarily a
code-review marker that the streaming path exists.

P1-T2, P1-SC-005. All 5 dedicated CH-source tests now green."
```

---

## Task 8: Extend cross-backend matrix to 16 cells

Mechanical extension. Adds `clickhouse_table` to `SOURCE_KINDS`, wires in seed/teardown for the new source kind. Matrix expands from 12 → 16 parametrize cells automatically.

**Files:**
- Modify: `python/tests/chunkshop/test_cross_backend_matrix.py`

- [ ] **Step 8.1: Add `_seed_ch` source helper**

Edit `python/tests/chunkshop/test_cross_backend_matrix.py`. After the existing `_seed_sqlite` function (around line 76), add:

```python
def _seed_ch(dsn_env: str, db: str) -> None:
    be = ClickHouseBackend(dsn_env=dsn_env)
    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{db}` SYNC")
        client.command(f"CREATE DATABASE `{db}`")
        client.command(
            f"CREATE TABLE `{db}`.`docs` "
            f"(id String, body String) ENGINE = MergeTree() ORDER BY id"
        )
        client.insert(
            f"`{db}`.`docs`",
            [["doc1", "Hello world. This is sentence two. " * 10]],
            column_names=["id", "body"],
        )
```

- [ ] **Step 8.2: Add `clickhouse_table` to SOURCE_KINDS**

Locate (around line 123):
```python
SOURCE_KINDS = ["pg_table", "mariadb_table", "sqlite_table"]
```

Replace with:
```python
SOURCE_KINDS = ["pg_table", "mariadb_table", "sqlite_table", "clickhouse_table"]
```

- [ ] **Step 8.3: Add `clickhouse_table` import + branch in `_build_source`**

Edit the imports near line 23 to include `ClickhouseTableSource`:

Before:
```python
from chunkshop.config import (
    PgTableSource, MariaDbTableSource, SqliteTableSource,
    TargetConfig, FastembedEmbedder, NoneExtractor,
    SentenceAwareChunker, IdentityFramerConfig, RuntimeConfig, CellConfig,
)
```

After:
```python
from chunkshop.config import (
    PgTableSource, MariaDbTableSource, SqliteTableSource, ClickhouseTableSource,
    TargetConfig, FastembedEmbedder, NoneExtractor,
    SentenceAwareChunker, IdentityFramerConfig, RuntimeConfig, CellConfig,
)
```

In `_build_source` (around line 127), after the `sqlite_table` branch, add:

```python
    if src_kind == "clickhouse_table":
        return ClickhouseTableSource(
            type="clickhouse_table", dsn_env=src_dsn_env, database=src_db_name,
            table="docs", id_column="id", content_column="body",
        )
```

- [ ] **Step 8.4: Add seed + teardown switches in the test body**

In `test_cross_backend_matrix` (around line 162-170), the source-kind dispatch currently ends with the sqlite case. Replace the existing source-side seed block:

Before:
```python
    if src_kind == "pg_table":
        _seed_pg("CHUNKSHOP_TEST_DSN", src_db_name)
        src_dsn = "CHUNKSHOP_TEST_DSN"
    elif src_kind == "mariadb_table":
        _seed_mariadb("CHUNKSHOP_TEST_DSN_MARIADB", src_db_name)
        src_dsn = "CHUNKSHOP_TEST_DSN_MARIADB"
    else:
        _seed_sqlite("XBM_SRC_SQLITE")
        src_dsn = "XBM_SRC_SQLITE"
```

After:
```python
    if src_kind == "pg_table":
        _seed_pg("CHUNKSHOP_TEST_DSN", src_db_name)
        src_dsn = "CHUNKSHOP_TEST_DSN"
    elif src_kind == "mariadb_table":
        _seed_mariadb("CHUNKSHOP_TEST_DSN_MARIADB", src_db_name)
        src_dsn = "CHUNKSHOP_TEST_DSN_MARIADB"
    elif src_kind == "sqlite_table":
        _seed_sqlite("XBM_SRC_SQLITE")
        src_dsn = "XBM_SRC_SQLITE"
    else:  # clickhouse_table
        _seed_ch("CHUNKSHOP_TEST_DSN_CH", src_db_name)
        src_dsn = "CHUNKSHOP_TEST_DSN_CH"
```

In the `finally` block at the bottom, the source-side teardown currently ends with the mariadb case. Add a CH branch:

Before:
```python
    finally:
        if src_kind == "pg_table":
            _drop_pg("CHUNKSHOP_TEST_DSN", src_db_name)
        elif src_kind == "mariadb_table":
            _drop_mariadb("CHUNKSHOP_TEST_DSN_MARIADB", src_db_name)
```

After:
```python
    finally:
        if src_kind == "pg_table":
            _drop_pg("CHUNKSHOP_TEST_DSN", src_db_name)
        elif src_kind == "mariadb_table":
            _drop_mariadb("CHUNKSHOP_TEST_DSN_MARIADB", src_db_name)
        elif src_kind == "clickhouse_table":
            _drop_ch("CHUNKSHOP_TEST_DSN_CH", src_db_name)
```

(`_drop_ch` already exists from the sink-side teardown — used at line 217.)

- [ ] **Step 8.5: Run the matrix — should now be 16 cells**

Run:
```bash
uv run pytest tests/chunkshop/test_cross_backend_matrix.py -v
```
Expected: 16 PASSED (4 sources × 4 sinks). All four DSN env vars must be set; otherwise the file-level `pytestmark` will skip all 16.

If any cell fails, especially the `clickhouse_table → clickhouse` cell (same DSN, two databases): that's the spec's flagged "client-state collision" risk — surface as a backend-level concern in the commit message but **do not** attempt to fix inside P1.

- [ ] **Step 8.6: Commit**

```bash
git add python/tests/chunkshop/test_cross_backend_matrix.py
git commit -m "test(matrix): extend cross-backend matrix to full 16 cells

Adds clickhouse_table to SOURCE_KINDS. Mechanical extension: new
_seed_ch helper, _build_source branch, seed + teardown switches.
Matrix is now 4 sources × 4 sinks = 16 parametrized cells.

P1-SC-007, P1-SC-008. Closes V4-SC-002 from the v0.4.0 finishing
roadmap (full 16-cell Python matrix)."
```

---

## Task 9: Operator-facing sample YAML

**Files:**
- Create: `docs/samples/sample-clickhouse-source.yaml`

- [ ] **Step 9.1: Write the sample**

Create `docs/samples/sample-clickhouse-source.yaml`:

```yaml
# ClickHouse SOURCE example. Reads source documents from a CH table and
# writes vectors to PG. Cross-backend cell — demonstrates the new P1
# capability (chunkshop reading from ClickHouse).
#
# Requires:
#   - CH 24.10+ (docker compose -f docker-compose.test.yaml up -d clickhouse)
#   - Postgres + pgvector (docker compose -f docker-compose.test.yaml up -d postgres)
#   - A pre-existing CH source table populated with documents
#
# From the chunkshop repo root:
#   export CHUNKSHOP_DSN_CH=clickhouse://default:chpw@localhost:8124/default
#   export CHUNKSHOP_DSN_PG=postgresql://postgres:postgres@localhost:5434/chunkshop
#   chunkshop ingest --config docs/samples/sample-clickhouse-source.yaml
#
# Notes on CH source semantics:
#   - `where` is trusted operator input — interpolated raw into the SELECT.
#     CH SQL dialect example below.
#   - If your source table is ReplacingMergeTree, unmerged duplicates may
#     surface as separate documents. Use a CH view with `SELECT ... FINAL`
#     if dedup matters at read time.
#   - JOIN-via-VIEW pattern works on CH the same as on PG/MariaDB —
#     define a CREATE VIEW IF NOT EXISTS in your DB, then point
#     `source.table` at the view name.
cell_name: samples_clickhouse_source_demo

source:
  type: clickhouse_table
  dsn_env: CHUNKSHOP_DSN_CH
  database: my_app
  table: documents
  id_column: id
  content_column: body
  title_column: title           # optional
  metadata_columns: [lang, author]
  # CH SQL dialect predicate (uncomment to filter):
  # where: "created_at > toDateTime('2025-01-01 00:00:00')"

chunker:
  type: hierarchy
  prefix_heading: true
  min_section_chars: 100

embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 2
  batch_size: 32

extractor:
  type: none

target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN_PG
  database: chunkshop_samples
  table: docs_from_clickhouse
  mode: overwrite
  source_tag: ch-source-demo
  hnsw: true

runtime:
  omp_num_threads: 2
```

- [ ] **Step 9.2: Validate the sample parses against the config schema**

Run:
```bash
uv run python -c "
from chunkshop.config import load_cell_config
cfg = load_cell_config('../docs/samples/sample-clickhouse-source.yaml')
print('cell_name:', cfg.cell_name)
print('source.type:', cfg.source.type)
print('source.where:', cfg.source.where)
print('target.type:', cfg.target.type)
"
```
Expected: prints the four lines without error. `source.where` is `None` (commented out).

If `load_cell_config` is named differently, check `python/src/chunkshop/config.py` for the loader function name and adjust.

- [ ] **Step 9.3: Run any sample-corpus test that picks the file up**

Run:
```bash
uv run pytest tests/chunkshop/test_end_to_end_samples_corpus.py -v 2>&1 | tail -20
```
Expected: existing tests still pass. If the sample is auto-picked by glob and the test attempts to actually run it, it will likely fail because there's no real `my_app.documents` source table — that's fine; if the test framework runs samples, the new one belongs in a "validate-only" tier. Inspect the test output and decide:
- If the test only validates schema (parses YAML → config), it passes.
- If the test runs end-to-end, the new sample needs a `# pytest: skip` marker convention or an exclusion. Check the test for an explicit skip-list and add the new file there if needed.

- [ ] **Step 9.4: Commit**

```bash
git add docs/samples/sample-clickhouse-source.yaml
git commit -m "docs(samples): add ClickHouse-as-source example YAML

Cross-backend cell — reads source docs from CH, writes vectors to PG.
Operator-facing sample analogous to the existing sink-side
sample-clickhouse.yaml. Documents the trusted-where contract,
JOIN-via-VIEW pattern, and ReplacingMergeTree gotcha.

P1-SC-009."
```

---

## Task 10: Final verification

- [ ] **Step 10.1: Run the full test suite**

Run:
```bash
uv run pytest -q
```
Expected: all tests green. New tests added in this plan: 5 in `test_source_clickhouse.py` + 5 config tests in `test_config_clickhouse_source.py` + 4 net-new matrix cells (4 new clickhouse_table-source × 4 sinks). No regressions in existing tests.

- [ ] **Step 10.2: Verify SC checklist**

Walk through each P1-SC item in the spec (`docs/superpowers/specs/2026-05-05-p1-py-clickhouse-source-design.md` §10) and confirm evidence:

| ID | Evidence |
|---|---|
| P1-SC-001 | `test_minimum_valid_config_parses`, `test_full_config_parses`, `test_typo_rejected_extra_forbid`, `test_wrong_type_rejected` all green |
| P1-SC-002 | `test_load_source_dispatches_clickhouse_table` green; transitively covered by every matrix cell |
| P1-SC-003 | `test_p1_t1_iter_documents_happy_path`, `test_p1_t5_title_column_optional` green |
| P1-SC-004 | `test_p1_t3_json_safe_recursive_coercion` green |
| P1-SC-005 | `test_p1_t2_streaming_does_not_materialize` green; `query_rows_stream` visible in `clickhouse_table.py` source code |
| P1-SC-006 | `test_p1_t4_where_clause_trusted_input` green |
| P1-SC-007 | `test_cross_backend_matrix` shows 16 PASSED |
| P1-SC-008 | Same matrix run; 12 pre-existing cells still green |
| P1-SC-009 | `sample-clickhouse-source.yaml` parses via `load_cell_config` |
| P1-SC-010 | This spec + plan explicitly note OQ4 closure (Task 3 commit message + spec §3, §4.2) |

- [ ] **Step 10.3: Write the summary**

Use the `summary-pattern.md` rule format. Output a CHANGES MADE / DIDN'T TOUCH / POTENTIAL CONCERNS block to the conversation. Example shape:

```
CHANGES MADE:
- python/pyproject.toml: added [clickhouse] extra (was missing despite v4 design); updated all-backends
- python/src/chunkshop/config.py: added ClickhouseTableSource model + extended SourceConfig union
- python/src/chunkshop/sources/__init__.py: imported new cfg + impl, added load_source dispatch branch
- python/src/chunkshop/sources/clickhouse_table.py: new — streaming source via query_rows_stream + recursive _json_safe
- python/tests/chunkshop/test_config_clickhouse_source.py: new — 5 config-load tests
- python/tests/chunkshop/test_source_clickhouse.py: new — 5 integration tests (T1-T5)
- python/tests/chunkshop/test_cross_backend_matrix.py: extended SOURCE_KINDS to 4 entries (12 → 16 cells)
- docs/samples/sample-clickhouse-source.yaml: new — operator-facing CH-as-source example

THINGS I DIDN'T TOUCH (intentionally):
- pg_table.py / mariadb_table.py / sqlite_table.py: trusted-where comment harmonization tempting; out of scope
- backends/clickhouse.py: would have blast radius into existing CH sink; out of scope
- PG/MariaDB sibling sources' streaming pattern: separate cross-source ticket if ever
- Source-side identifier regex on CH: should be all-source change if done at all

POTENTIAL CONCERNS:
- [if observed] Map(String, UUID) insert via clickhouse-connect dict-binding: works/required-workaround per Task 4 outcome
- [if observed] CH-source × CH-sink cell client-state collision (same DSN, two DBs): backend-level, not P1
- The streaming smoke test (P1-T2) is admittedly soft — primarily a code-review marker
- OQ4 from v4 design is now closed-as-non-issue; the predecessor spec table entry could be retroactively annotated in a follow-up
```

- [ ] **Step 10.4: Final commit (if anything left uncommitted)**

```bash
git status
```
Expected: clean tree. If anything remains uncommitted, stage and commit it before declaring done.

- [ ] **Step 10.5: Mission-brief drift checks (DC-FINAL)**

Re-read the spec one more time. For each DC-1..DC-4 in §11, confirm the question was answered during execution. Note any deviation in the summary.

---

## Self-review notes

**Spec coverage verified:**
- §1 goal — Tasks 2-3 (source impl); Task 8 (matrix expansion)
- §2 non-goals — none of these are implemented (✓)
- §3 inherited decisions — followed throughout
- §4 architecture (file map) — Tasks 1-9 cover every file in the file map
- §5 components — Task 2 (config), Task 3 (impl + _json_safe), Task 2 (load_source branch)
- §6 data flow — exercised by P1-T1 in Task 3
- §7 error handling & identifier safety — covered by P1-T1, P1-T3, P1-T4 (operator-trusted contract); driver-error-passthrough is the absence of try/except in Task 3 code
- §8 test strategy — Tasks 3-7 (5 dedicated tests); Task 8 (matrix); Task 9 (sample validation)
- §9 sample YAML — Task 9
- §10 success criteria — all 10 P1-SC items mapped in Step 10.2
- §11 drift checkpoints — Step 10.5
- §12 constraints — followed (no sibling-source edits; no `ClickHouseBackend` edits; only the 5-test scope plus the prerequisite `[clickhouse]` extra in Task 1, which is a legitimate scope addition surfaced during context exploration)
- §13 OOS — none of these are implemented

**Note on Task 1 (the `[clickhouse]` extra):** this isn't in the spec's file map but is a real prerequisite discovered during plan-writing. The CH backend/sink already exist on this branch but the extra was never added to `pyproject.toml`. Without it, `uv sync` won't install `clickhouse-connect`, and the new source can't run. Folding it into P1 is appropriate; it's a 4-line `pyproject.toml` change with zero risk.

**Placeholder scan:** every code block is complete; every `Run:` step has a concrete command + expected output; no "implement appropriate validation" / "handle errors" hand-waves.

**Type consistency:** class name `ClickhouseTableSource` consistent everywhere (matches sibling `MariaDbTableSource` casing — second word lowercase second-letter); discriminator value `clickhouse_table` consistent; config alias `database_name` from `Field(alias="database")` matches sibling pattern.
