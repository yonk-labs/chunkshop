# v4.0 Modular Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor chunkshop's Postgres-only storage layer into a modular `backends/` + `sinks/` + `sources/` shape, then add SQLite (with `sqlite-vec`) and MariaDB as second and third backends, proving symmetric source+sink modularity across three different storage models (server OLTP, embedded file, server with native VECTOR type) without breaking existing PG behavior.

**Architecture:** New `backends/` package owns connection lifecycle, identifier safety, dialect helpers, and DDL sequencing. Per-backend `sinks/<name>.py` and `sources/<name>_table.py` own their chunkshop-specific data-model semantics (modes, metadata promotion, delete_orphans, source-tag write-once). YAML breaks: `target.type: postgres` discriminator added; `schema:` field renamed to `database:`. **SQLite-specific:** chunks-table is split into a regular `<table>` plus a virtual `<table>_vec` (`sqlite-vec` vec0 virtual table) joined on `id` — the Sink owns this two-table dance.

**Tech Stack:** Python 3.11+, pydantic v2 (discriminated unions), psycopg 3 (PG), `sqlite3` stdlib + `sqlite-vec>=0.1.6` extension (SQLite), PyMySQL 1.1+ (MariaDB ≥11.7), pytest. ClickHouse design-supports-it but not built in this plan.

**Build order:** Phases 0–3 land the abstraction + PG refactor. Phase 4–5 add SQLite (smaller surface, no infrastructure dependency, fastest proof of flexibility). Phase 6–8 add MariaDB. Phase 9 wires cross-backend tests + final SC verification.

**Spec:** `docs/superpowers/specs/2026-04-30-v4-modular-backends-design.md`

**Worktree:** `/home/yonk/yonk-tools/chunkshop-v4` on `experimental/v4-modular-backends`. All commands assume `cd python/` from this worktree root unless noted.

---

## Phase 0 — Pre-flight

### Task 0: Verify clean baseline on PG

**Files:**
- Read: `python/pyproject.toml`, current test status

- [ ] **Step 1: Verify worktree is at expected commit**

```bash
git -C /home/yonk/yonk-tools/chunkshop-v4 log --oneline -3
```

Expected: top commit is `docs(spec): v4.0 modular-backends design — PG-refactor + MariaDB first ship`.

- [ ] **Step 2: Install with extractors extra and run baseline tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-v4/python
uv sync --extra dev --extra extractors
uv run pytest -q 2>&1 | tail -20
```

Expected: tests pass (some skip if `$CHUNKSHOP_TEST_DSN` unreachable). Record passing count as the "must not regress" baseline.

- [ ] **Step 3: No commit** — this task is verification only.

---

## Phase 1 — `backends/` infrastructure + Postgres backend

### Task 1: Create `backends/base.py` with `Backend` Protocol + `ColSpec`

**Files:**
- Create: `python/src/chunkshop/backends/__init__.py` (empty for now)
- Create: `python/src/chunkshop/backends/base.py`
- Test: `python/tests/chunkshop/test_backends_base.py`

- [ ] **Step 1: Write the failing test**

```python
# python/tests/chunkshop/test_backends_base.py
import pytest
from dataclasses import FrozenInstanceError
from chunkshop.backends.base import ColSpec, Backend


def test_colspec_is_frozen():
    c = ColSpec(name="id", type_ddl="text", nullable=False, is_primary_key=True)
    assert c.name == "id"
    assert c.is_primary_key is True
    with pytest.raises(FrozenInstanceError):
        c.name = "different"


def test_colspec_defaults():
    c = ColSpec(name="metadata", type_ddl="jsonb")
    assert c.nullable is True
    assert c.default is None
    assert c.is_primary_key is False


def test_backend_protocol_lists_required_attrs():
    # Protocol membership is structural; just verify the Protocol class exists
    # and the documented attrs are referenced. This test is a hedge against
    # accidental rename.
    for attr in ("name", "connect", "quote_ident", "fq_table",
                 "vector_type_ddl", "json_type_ddl", "tags_array_type_ddl",
                 "vector_literal", "tags_literal", "json_literal",
                 "json_path_sql", "supports_upsert", "upsert_clause",
                 "create_database_sql", "add_column_if_not_exists_sql",
                 "drop_table_sql", "emit_chunks_table_ddl",
                 "table_exists", "embedding_dim", "with_create_lock"):
        assert hasattr(Backend, attr), f"Backend missing attribute: {attr}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/chunkshop/test_backends_base.py -v
```

Expected: `ModuleNotFoundError: No module named 'chunkshop.backends'`.

- [ ] **Step 3: Create the module**

```python
# python/src/chunkshop/backends/__init__.py
"""Backend layer: connection lifecycle + dialect helpers per database backend."""
from chunkshop.backends.base import Backend, ColSpec

__all__ = ["Backend", "ColSpec"]
```

```python
# python/src/chunkshop/backends/base.py
"""Backend Protocol + shared dataclasses.

Backends own everything that MUST be different per backend, including DDL
sequencing. Sinks own chunkshop-specific data-model semantics (modes,
metadata promotion, delete_orphans, source-tag write-once).
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, ContextManager, Iterator, Literal, Protocol

import numpy as np


@dataclass(frozen=True)
class ColSpec:
    """One column in a chunks table — backend-agnostic shape, backend-specific type DDL."""
    name: str
    type_ddl: str           # backend-specific type fragment, e.g. "text" / "VARCHAR(255)"
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False


class Backend(Protocol):
    """One backend = one DB engine. Stateless; methods are pure helpers + a connect ctx-mgr."""

    name: Literal["postgres", "mariadb", "clickhouse"]
    supports_upsert: bool       # CH = False; PG/MariaDB = True

    # Connection lifecycle
    @contextmanager
    def connect(self) -> Iterator[Any]: ...   # yields driver-native connection

    # Identifier safety
    def quote_ident(self, name: str) -> str: ...
    def fq_table(self, db: str, table: str) -> str: ...

    # Type DDL fragments
    def vector_type_ddl(self, dim: int) -> str: ...
    def json_type_ddl(self) -> str: ...
    def tags_array_type_ddl(self) -> str: ...
    def text_pk_type_ddl(self) -> str: ...
    def timestamp_now_default_ddl(self) -> str: ...

    # Value literals (returned as parameter-bindable Python values for the driver)
    def vector_literal(self, arr: np.ndarray) -> Any: ...
    def tags_literal(self, tags: list[str]) -> Any: ...
    def json_literal(self, obj: Any) -> Any: ...

    # JSON dotted-path extraction (used by promote_metadata + metadata_columns)
    def json_path_sql(self, col_expr: str, dotted_path: str) -> str: ...

    # Upsert / conflict handling
    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str: ...

    # DDL primitives
    def create_database_sql(self, name: str) -> str: ...
    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str: ...
    def drop_table_sql(self, fq: str) -> str: ...

    # Composite DDL — backend handles HNSW timing differences
    def emit_chunks_table_ddl(
        self,
        fq: str,
        cols: list[ColSpec],
        hnsw: bool,
        dim: int,
        engine: str | None = None,
    ) -> list[str]: ...

    # Introspection
    def table_exists(self, cur: Any, db: str, table: str) -> bool: ...
    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None: ...

    # Concurrent-create serialization (some backends are no-op)
    def with_create_lock(self, cur: Any, key: str) -> ContextManager[None]: ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/chunkshop/test_backends_base.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/backends/__init__.py \
        python/src/chunkshop/backends/base.py \
        python/tests/chunkshop/test_backends_base.py
git commit -m "feat(backends): add Backend Protocol + ColSpec dataclass"
```

---

### Task 2: `backends/postgres.py` — connection lifecycle + identifier safety

**Files:**
- Create: `python/src/chunkshop/backends/postgres.py`
- Test: `python/tests/chunkshop/test_backend_postgres.py`

- [ ] **Step 1: Write failing tests**

```python
# python/tests/chunkshop/test_backend_postgres.py
import os
import pytest
from chunkshop.backends.postgres import PostgresBackend


@pytest.fixture
def be():
    return PostgresBackend(dsn_env="DUMMY_DSN_NOT_USED_HERE")


def test_name_and_supports_upsert(be):
    assert be.name == "postgres"
    assert be.supports_upsert is True


def test_quote_ident_simple(be):
    assert be.quote_ident("my_table") == '"my_table"'


def test_quote_ident_escapes_embedded_double_quote(be):
    # Postgres identifier escaping: " becomes ""
    assert be.quote_ident('weird"name') == '"weird""name"'


def test_fq_table_joins_schema_and_table(be):
    assert be.fq_table("chunkshop", "my_chunks") == '"chunkshop"."my_chunks"'


def test_connect_reads_from_env(monkeypatch):
    monkeypatch.setenv("PG_TEST_DSN", "postgresql://nosuchhost:1/x")
    be = PostgresBackend(dsn_env="PG_TEST_DSN")
    # Don't actually connect — just check the DSN was wired. The full connect
    # path is exercised by the sink integration tests when $CHUNKSHOP_TEST_DSN
    # is set.
    assert be._dsn == "postgresql://nosuchhost:1/x"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py -v
```

Expected: `ModuleNotFoundError: No module named 'chunkshop.backends.postgres'`.

- [ ] **Step 3: Create the module (initial slice — connect + idents)**

```python
# python/src/chunkshop/backends/postgres.py
"""Postgres backend: psycopg-based connection + dialect helpers."""
from __future__ import annotations
import os
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import psycopg


class PostgresBackend:
    """Backend Protocol implementation for Postgres + pgvector."""

    name: Literal["postgres"] = "postgres"
    supports_upsert: bool = True

    def __init__(self, dsn_env: str):
        self._dsn_env = dsn_env
        self._dsn = os.environ.get(dsn_env, "")

    # Connection lifecycle
    @contextmanager
    def connect(self) -> Iterator[Any]:
        # Per CLAUDE.md: short-lived per-document connections (deliberate).
        dsn = os.environ[self._dsn_env]
        with psycopg.connect(dsn) as conn:
            yield conn

    # Identifier safety — PG-style: wrap in double quotes, escape embedded "
    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def fq_table(self, db: str, table: str) -> str:
        return f'{self.quote_ident(db)}.{self.quote_ident(table)}'
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/backends/postgres.py \
        python/tests/chunkshop/test_backend_postgres.py
git commit -m "feat(backends/postgres): connection ctx-mgr + identifier safety"
```

---

### Task 3: `backends/postgres.py` — type DDL fragments + value literals + JSON path

**Files:**
- Modify: `python/src/chunkshop/backends/postgres.py`
- Modify: `python/tests/chunkshop/test_backend_postgres.py`

- [ ] **Step 1: Append failing tests**

```python
# Append to python/tests/chunkshop/test_backend_postgres.py
import json
import numpy as np


def test_vector_type_ddl(be):
    assert be.vector_type_ddl(384) == "vector(384)"


def test_json_type_ddl(be):
    assert be.json_type_ddl() == "jsonb"


def test_tags_array_type_ddl(be):
    assert be.tags_array_type_ddl() == "text[]"


def test_text_pk_type_ddl(be):
    assert be.text_pk_type_ddl() == "text"


def test_timestamp_now_default_ddl(be):
    assert "now()" in be.timestamp_now_default_ddl().lower()


def test_vector_literal_format(be):
    arr = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = be.vector_literal(arr)
    # PG expects '[0.1,0.2,0.3]' with optional ::vector cast applied at SQL time
    assert out.startswith("[") and out.endswith("]")
    assert "0.1" in out and "0.2" in out and "0.3" in out


def test_tags_literal_passthrough(be):
    # PG uses native arrays — psycopg adapts list[str] → text[] directly
    assert be.tags_literal(["a", "b"]) == ["a", "b"]


def test_json_literal_serializes(be):
    out = be.json_literal({"a": 1, "b": [2, 3]})
    # json.dumps is idempotent for round-trip
    assert json.loads(out) == {"a": 1, "b": [2, 3]}


def test_json_path_sql_simple_key(be):
    # Single-key path
    assert be.json_path_sql("metadata", "lang") == "metadata->>'lang'"


def test_json_path_sql_nested(be):
    # Multi-segment path traverses with -> then ->> at the leaf
    assert be.json_path_sql("metadata", "entities.ORG") == "metadata->'entities'->>'ORG'"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py -v -k "vector_type or json_type or tags_array or text_pk or timestamp_now or _literal or json_path"
```

Expected: 9 FAIL with `AttributeError`.

- [ ] **Step 3: Append implementation**

```python
# Append to python/src/chunkshop/backends/postgres.py
import json
import numpy as np


# In class PostgresBackend, after fq_table:

    # Type DDL fragments
    def vector_type_ddl(self, dim: int) -> str:
        return f"vector({dim})"

    def json_type_ddl(self) -> str:
        return "jsonb"

    def tags_array_type_ddl(self) -> str:
        return "text[]"

    def text_pk_type_ddl(self) -> str:
        return "text"

    def timestamp_now_default_ddl(self) -> str:
        return "timestamptz NOT NULL DEFAULT now()"

    # Value literals
    def vector_literal(self, arr: np.ndarray) -> str:
        # pgvector accepts a text literal; format with 6 decimal places to match
        # the precision used in the legacy sink.py code.
        return "[" + ",".join(f"{x:.6f}" for x in arr) + "]"

    def tags_literal(self, tags: list[str]) -> list[str]:
        # psycopg adapts list[str] to text[] natively — passthrough.
        return list(tags)

    def json_literal(self, obj: Any) -> str:
        return json.dumps(obj)

    # JSON dotted-path extraction
    def json_path_sql(self, col_expr: str, dotted_path: str) -> str:
        # PG idiom: -> for intermediate jsonb, ->> for the final text leaf.
        # Identifier validation upstream (PromoteColumn._safe_path) ensures
        # path segments match ^[A-Za-z_][A-Za-z0-9_]*$, so single-quoting
        # the segment is safe.
        segs = dotted_path.split(".")
        if len(segs) == 1:
            return f"{col_expr}->>'{segs[0]}'"
        # All but the last use ->, last uses ->>
        head = "->".join([col_expr] + [f"'{s}'" for s in segs[:-1]])
        return f"{head}->>'{segs[-1]}'"
```

Be careful when appending: place the new methods **inside** the `PostgresBackend` class body. Add `import json` and `import numpy as np` to the top of the file if not already present.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py -v
```

Expected: all 13 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/backends/postgres.py \
        python/tests/chunkshop/test_backend_postgres.py
git commit -m "feat(backends/postgres): type DDL fragments + value literals + json_path_sql"
```

---

### Task 4: `backends/postgres.py` — DDL primitives + upsert_clause

**Files:**
- Modify: `python/src/chunkshop/backends/postgres.py`
- Modify: `python/tests/chunkshop/test_backend_postgres.py`

- [ ] **Step 1: Append failing tests**

```python
# Append to python/tests/chunkshop/test_backend_postgres.py


def test_create_database_sql_uses_create_schema(be):
    # On PG the "database" concept maps to a SCHEMA inside the connected DB
    out = be.create_database_sql("chunkshop_test")
    assert "CREATE SCHEMA IF NOT EXISTS" in out
    assert '"chunkshop_test"' in out


def test_add_column_if_not_exists_sql(be):
    out = be.add_column_if_not_exists_sql('"db"."tbl"', "newcol", "text")
    assert "ALTER TABLE" in out
    assert "ADD COLUMN IF NOT EXISTS" in out
    assert '"newcol"' in out
    assert "text" in out


def test_drop_table_sql(be):
    out = be.drop_table_sql('"db"."tbl"')
    assert out.startswith("DROP TABLE")
    assert '"db"."tbl"' in out


def test_upsert_clause_pg_form(be):
    out = be.upsert_clause(["id"], ["content", "metadata"])
    assert "ON CONFLICT" in out and '("id")' in out
    assert 'DO UPDATE SET' in out
    assert '"content" = EXCLUDED."content"' in out
    assert '"metadata" = EXCLUDED."metadata"' in out


def test_upsert_clause_empty_update_cols(be):
    # If no columns to update, ON CONFLICT DO NOTHING is the safe fallback
    out = be.upsert_clause(["id"], [])
    assert "DO NOTHING" in out
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py -v -k "create_database or add_column or drop_table or upsert_clause"
```

Expected: 5 FAIL with `AttributeError`.

- [ ] **Step 3: Append implementation**

```python
# Append to PostgresBackend class:

    # DDL primitives
    def create_database_sql(self, name: str) -> str:
        # PG calls it SCHEMA; chunkshop's "database" config maps to PG schema.
        return f"CREATE SCHEMA IF NOT EXISTS {self.quote_ident(name)}"

    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str:
        return f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {self.quote_ident(col)} {type_ddl}"

    def drop_table_sql(self, fq: str) -> str:
        return f"DROP TABLE {fq}"

    # Upsert / conflict handling
    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str:
        keys = ", ".join(self.quote_ident(c) for c in key_cols)
        if not update_cols:
            return f"ON CONFLICT ({keys}) DO NOTHING"
        sets = ", ".join(
            f"{self.quote_ident(c)} = EXCLUDED.{self.quote_ident(c)}" for c in update_cols
        )
        return f"ON CONFLICT ({keys}) DO UPDATE SET {sets}"
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py -v
```

Expected: all 18 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/backends/postgres.py \
        python/tests/chunkshop/test_backend_postgres.py
git commit -m "feat(backends/postgres): DDL primitives + upsert_clause"
```

---

### Task 5: `backends/postgres.py` — `emit_chunks_table_ddl` (composite DDL with separate HNSW)

**Files:**
- Modify: `python/src/chunkshop/backends/postgres.py`
- Modify: `python/tests/chunkshop/test_backend_postgres.py`

- [ ] **Step 1: Append failing tests**

```python
# Append to python/tests/chunkshop/test_backend_postgres.py
from chunkshop.backends.base import ColSpec


def _canonical_cols():
    """The chunkshop-canonical column list — what Sink will pass to Backend."""
    return [
        ColSpec("id", "text", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "text", nullable=False),
        ColSpec("seq_num", "int", nullable=False),
        ColSpec("original_content", "text", nullable=False),
        ColSpec("embedded_content", "text", nullable=False),
        ColSpec("tags", "text[]", nullable=False, default="'{}'"),
        ColSpec("metadata", "jsonb", nullable=False, default="'{}'"),
        ColSpec("embedding", "vector(384)", nullable=False),
        ColSpec("source", "text"),
        ColSpec("created_at", "timestamptz", nullable=False, default="now()"),
    ]


def test_emit_chunks_table_ddl_returns_create_table_then_indexes(be):
    out = be.emit_chunks_table_ddl(
        fq='"db"."chunks"', cols=_canonical_cols(), hnsw=True, dim=384,
    )
    assert isinstance(out, list) and len(out) >= 2
    create = out[0]
    assert create.startswith("CREATE TABLE IF NOT EXISTS")
    assert '"id" text' in create
    assert "PRIMARY KEY" in create
    assert "vector(384)" in create
    # doc_id+seq_num index always present
    assert any("CREATE INDEX" in s and "doc_id" in s and "seq_num" in s for s in out[1:])
    # HNSW index when hnsw=True
    assert any("USING hnsw" in s and "vector_cosine_ops" in s for s in out[1:])


def test_emit_chunks_table_ddl_hnsw_false_omits_hnsw_index(be):
    out = be.emit_chunks_table_ddl(
        fq='"db"."chunks"', cols=_canonical_cols(), hnsw=False, dim=384,
    )
    assert not any("USING hnsw" in s for s in out)
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py -v -k "emit_chunks_table_ddl"
```

Expected: 2 FAIL with `AttributeError`.

- [ ] **Step 3: Append implementation**

```python
# Append to PostgresBackend class:

    def emit_chunks_table_ddl(
        self,
        fq: str,
        cols: list,  # list[ColSpec]
        hnsw: bool,
        dim: int,
        engine: str | None = None,
    ) -> list[str]:
        # Engine clause is a no-op on PG (engine is the cluster's, not table-level).
        del engine

        col_lines = []
        pk_cols = []
        for c in cols:
            line = f"  {self.quote_ident(c.name)} {c.type_ddl}"
            if c.default is not None:
                line += f" DEFAULT {c.default}"
            if not c.nullable:
                line += " NOT NULL"
            col_lines.append(line)
            if c.is_primary_key:
                pk_cols.append(c.name)

        lines = ",\n".join(col_lines)
        if pk_cols:
            pk = ", ".join(self.quote_ident(c) for c in pk_cols)
            lines += f",\n  PRIMARY KEY ({pk})"

        create = f"CREATE TABLE IF NOT EXISTS {fq} (\n{lines}\n)"

        # Strip schema prefix from fq for index naming: "db"."chunks" → chunks
        bare_table = fq.rsplit('.', 1)[-1].strip('"')
        statements = [create]
        statements.append(
            f'CREATE INDEX IF NOT EXISTS {self.quote_ident(bare_table + "_doc_seq_idx")} '
            f'ON {fq} ("doc_id", "seq_num")'
        )
        if hnsw:
            statements.append(
                f'CREATE INDEX IF NOT EXISTS {self.quote_ident(bare_table + "_emb_hnsw_idx")} '
                f'ON {fq} USING hnsw ("embedding" vector_cosine_ops)'
            )
        return statements
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py -v
```

Expected: all 20 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/backends/postgres.py \
        python/tests/chunkshop/test_backend_postgres.py
git commit -m "feat(backends/postgres): emit_chunks_table_ddl composite (table + indexes)"
```

---

### Task 6: `backends/postgres.py` — introspection + concurrent-create lock + `load_backend` factory

**Files:**
- Modify: `python/src/chunkshop/backends/postgres.py`
- Modify: `python/src/chunkshop/backends/__init__.py`
- Modify: `python/tests/chunkshop/test_backend_postgres.py`
- Test: `python/tests/chunkshop/test_backends_load.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# Append to python/tests/chunkshop/test_backend_postgres.py
import hashlib


def test_advisory_lock_key_is_stable(be):
    # The lock key is derived from blake2b so two processes hashing the same
    # schema get the same int — verify by re-deriving it.
    expected = int.from_bytes(
        hashlib.blake2b(b"chunkshop_test", digest_size=8).digest(),
        "big", signed=True,
    )
    assert be._advisory_lock_key("chunkshop_test") == expected
```

```python
# python/tests/chunkshop/test_backends_load.py
import pytest
from chunkshop.backends import load_backend


def test_load_backend_postgres():
    be = load_backend(name="postgres", dsn_env="DUMMY_DSN")
    assert be.name == "postgres"


def test_load_backend_unknown():
    with pytest.raises(ValueError, match="unknown backend"):
        load_backend(name="oracle", dsn_env="X")
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py::test_advisory_lock_key_is_stable tests/chunkshop/test_backends_load.py -v
```

Expected: 3 FAIL — first with `AttributeError`, others with `ImportError`.

- [ ] **Step 3: Implement**

```python
# Append to PostgresBackend class in python/src/chunkshop/backends/postgres.py
import hashlib
import re
from contextlib import contextmanager


# (in class body)

    # Introspection
    def table_exists(self, cur: Any, db: str, table: str) -> bool:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=%s AND tablename=%s)",
            (db, table),
        )
        return cur.fetchone()[0]

    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None:
        # Robust to pgvector version: format_type yields "vector(N)" regardless of
        # atttypmod-plus-VARHDRSZ encoding. Works on empty tables.
        cur.execute(
            """
            SELECT format_type(atttypid, atttypmod)
            FROM pg_attribute
            WHERE attrelid = (
                SELECT c.oid FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = %s AND n.nspname = %s
            ) AND attname = 'embedding'
            """,
            (table, db),
        )
        r = cur.fetchone()
        if r is None:
            return None
        m = re.match(r"^vector\((\d+)\)$", r[0])
        return int(m.group(1)) if m else None

    # Concurrent-create lock — PG uses pg_advisory_xact_lock keyed on a stable hash
    @staticmethod
    def _advisory_lock_key(name: str) -> int:
        # blake2b is stable across processes (unlike Python's PYTHONHASHSEED-randomized hash())
        digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big", signed=True)

    @contextmanager
    def with_create_lock(self, cur: Any, key: str) -> Iterator[None]:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (self._advisory_lock_key(key),))
        try:
            yield
        finally:
            # xact_lock releases on commit/rollback automatically; nothing to do here
            pass
```

```python
# python/src/chunkshop/backends/__init__.py
"""Backend layer: connection lifecycle + dialect helpers per database backend."""
from chunkshop.backends.base import Backend, ColSpec
from chunkshop.backends.postgres import PostgresBackend


def load_backend(name: str, dsn_env: str) -> Backend:
    """Factory: return the Backend impl for the given name."""
    if name == "postgres":
        return PostgresBackend(dsn_env=dsn_env)
    # Future: "mariadb" → MariaDBBackend (Phase 4); "clickhouse" → out of scope this plan
    raise ValueError(f"unknown backend: {name!r}")


__all__ = ["Backend", "ColSpec", "PostgresBackend", "load_backend"]
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/chunkshop/test_backend_postgres.py tests/chunkshop/test_backends_load.py -v
```

Expected: all PASS (21 + 2 = 23).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/backends/__init__.py \
        python/src/chunkshop/backends/postgres.py \
        python/tests/chunkshop/test_backend_postgres.py \
        python/tests/chunkshop/test_backends_load.py
git commit -m "feat(backends): introspection + advisory lock + load_backend factory"
```

---

## Phase 2 — `Sink` Protocol + Postgres sink

### Task 7: Create `sinks/base.py` with `Sink` Protocol

**Files:**
- Create: `python/src/chunkshop/sinks/__init__.py`
- Create: `python/src/chunkshop/sinks/base.py`
- Test: `python/tests/chunkshop/test_sinks_base.py`

- [ ] **Step 1: Write failing test**

```python
# python/tests/chunkshop/test_sinks_base.py
from chunkshop.sinks.base import Sink


def test_sink_protocol_lists_required_methods():
    for attr in ("create_table", "write_document", "count_docs"):
        assert hasattr(Sink, attr)
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/chunkshop/test_sinks_base.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create modules**

```python
# python/src/chunkshop/sinks/__init__.py
"""Sink layer: per-backend writers for chunkshop's chunks table."""
from chunkshop.sinks.base import Sink

__all__ = ["Sink"]
```

```python
# python/src/chunkshop/sinks/base.py
"""Sink Protocol — every Sink owns chunkshop's data-model semantics on its backend."""
from __future__ import annotations
from typing import Protocol

import numpy as np

from chunkshop.chunkers.base import Chunk


class Sink(Protocol):
    def create_table(self) -> None: ...
    def write_document(
        self,
        doc_id: str,
        chunks: list[Chunk],
        embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None: ...
    def count_docs(self) -> int: ...
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/chunkshop/test_sinks_base.py -v
```

Expected: 1 PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sinks/__init__.py \
        python/src/chunkshop/sinks/base.py \
        python/tests/chunkshop/test_sinks_base.py
git commit -m "feat(sinks): Sink Protocol skeleton"
```

---

### Task 8: `sinks/pg.py` — port `PgVectorSink` from `sink.py`, delegating dialect to Backend

**Files:**
- Create: `python/src/chunkshop/sinks/pg.py`
- Reference (read, do not modify yet): `python/src/chunkshop/sink.py` — port from here

This task is the heaviest single port. The strategy: copy the existing `PgVectorSink` class, then replace each Postgres-specific call with a Backend call. The `_jsonb_path_get` helper (current `sink.py:27-37`) stays in `sinks/pg.py` because it's about navigating Python dicts in memory, not SQL.

- [ ] **Step 1: Create the new file by porting from sink.py**

Read `python/src/chunkshop/sink.py` fully. Create `python/src/chunkshop/sinks/pg.py` with the following structure (paths annotated for what to port from):

```python
# python/src/chunkshop/sinks/pg.py
"""Postgres sink — pgvector chunks-table writer using the PostgresBackend dialect."""
from __future__ import annotations
import json
import os
from typing import Any

import numpy as np
import psycopg
from psycopg import sql

from chunkshop.backends.base import ColSpec
from chunkshop.backends.postgres import PostgresBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig


def _jsonb_path_get(meta: dict, path: str):
    """Walk a dotted path through nested dicts; return None if any segment missing.

    Ported from sink.py:27-37 — chunkshop-specific dict navigation, not SQL.
    """
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _canonical_cols(dim: int) -> list[ColSpec]:
    """The chunkshop-canonical chunks-table column list, PG-typed."""
    return [
        ColSpec("id", "text", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "text", nullable=False),
        ColSpec("seq_num", "int", nullable=False),
        ColSpec("original_content", "text", nullable=False),
        ColSpec("embedded_content", "text", nullable=False),
        ColSpec("tags", "text[]", nullable=False, default="'{}'"),
        ColSpec("metadata", "jsonb", nullable=False, default="'{}'"),
        ColSpec("embedding", f"vector({dim})", nullable=False),
        ColSpec("source", "text"),
        ColSpec("created_at", "timestamptz", nullable=False, default="now()"),
    ]


class PgSink:
    """Per-document writer to a Postgres chunks table.

    Wraps the canonical chunkshop data model (id/doc_id/seq_num/original_content/
    embedded_content/tags/metadata/embedding/source/created_at + promoted columns).
    Owns mode dispatch (overwrite/append/create_if_missing), foreign-tag safety,
    append preflight, source write-once, and delete_orphans. Delegates all
    dialect/connection/identifier work to PostgresBackend.
    """

    def __init__(self, cfg: TargetConfig, backend: PostgresBackend, embed_dim: int):
        self.cfg = cfg
        self.backend = backend
        self.embed_dim = embed_dim
        self._dsn = os.environ[cfg.dsn_env]

    def _fq(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table)

    # -- create_table dispatch ----------------------------------------------
    def create_table(self) -> None:
        with self.backend.connect() as conn, conn.cursor() as cur:
            with self.backend.with_create_lock(cur, self.cfg.database_name):
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(self.backend.create_database_sql(self.cfg.database_name))

                if self.cfg.mode == "overwrite":
                    self._overwrite_create(cur)
                elif self.cfg.mode == "append":
                    self._append_preflight(cur)
                elif self.cfg.mode == "create_if_missing":
                    self._create_if_missing(cur)
                else:
                    raise ValueError(f"unknown mode: {self.cfg.mode}")
            conn.commit()

    def _create_base_ddl(self, cur) -> None:
        for stmt in self.backend.emit_chunks_table_ddl(
            fq=self._fq(),
            cols=_canonical_cols(self.embed_dim),
            hnsw=self.cfg.hnsw,
            dim=self.embed_dim,
        ):
            cur.execute(stmt)
        self._ensure_promote_columns(cur)

    def _ensure_promote_columns(self, cur) -> None:
        for pc in self.cfg.promote_metadata:
            cur.execute(self.backend.add_column_if_not_exists_sql(
                self._fq(), pc.column_name, pc.type
            ))

    def _overwrite_create(self, cur) -> None:
        # Foreign-tag safety: refuse to drop a table holding rows from a different source_tag.
        if self._table_exists(cur) and not self.cfg.force_overwrite:
            cur.execute(
                f"SELECT DISTINCT source FROM {self._fq()} WHERE source IS NOT NULL LIMIT 10"
            )
            existing_tags = {r[0] for r in cur.fetchall()}
            my_tag = self.cfg.source_tag
            foreign = existing_tags - ({my_tag} if my_tag else set())
            if foreign:
                raise RuntimeError(
                    f"overwrite refuses to drop {self.cfg.database_name}.{self.cfg.table}: "
                    f"table holds rows with source_tag values {sorted(foreign)!r} that differ "
                    f"from this cell's source_tag {my_tag!r}. Set target.force_overwrite: true "
                    f"in YAML to bypass."
                )
        if self._table_exists(cur):
            cur.execute(self.backend.drop_table_sql(self._fq()))
        self._create_base_ddl(cur)

    def _create_if_missing(self, cur) -> None:
        if not self._table_exists(cur):
            self._create_base_ddl(cur)
        else:
            cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "text"))
            self._ensure_promote_columns(cur)

    def _append_preflight(self, cur) -> None:
        if not self._table_exists(cur):
            raise RuntimeError(
                f"append mode: table {self.cfg.database_name}.{self.cfg.table} does not exist. "
                f"Use mode='create_if_missing' on the first cell."
            )
        current_dim = self.backend.embedding_dim(cur, self.cfg.database_name, self.cfg.table)
        if current_dim is None:
            raise RuntimeError(
                f"append mode: table {self.cfg.database_name}.{self.cfg.table} exists but has no "
                f"'embedding' vector column. This does not appear to be a chunkshop target table."
            )
        if current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target embedding dim is {current_dim}, cell's embedder dim is "
                f"{self.embed_dim}. Vectors are not comparable."
            )
        cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "text"))
        self._ensure_promote_columns(cur)

    def _table_exists(self, cur) -> bool:
        return self.backend.table_exists(cur, self.cfg.database_name, self.cfg.table)

    # -- write_document -----------------------------------------------------
    def write_document(
        self, doc_id: str, chunks: list[Chunk], embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) length mismatch")
        if len(chunks) != len(tags_per_chunk):
            raise ValueError(f"chunks ({len(chunks)}) and tags ({len(tags_per_chunk)}) length mismatch")

        promote = self.cfg.promote_metadata
        base_col_names = [
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "embedding", "source",
        ]
        all_col_names = base_col_names + [pc.column_name for pc in promote]

        # update_cols: skip id/doc_id/seq_num AND source (source is write-once).
        update_cols = base_col_names[3:8] + [pc.column_name for pc in promote]
        upsert_sql = self.backend.upsert_clause(["id"], update_cols)

        cols_sql = ", ".join(self.backend.quote_ident(c) for c in all_col_names)
        # PG-specific value placeholders: jsonb cast, vector cast
        placeholders = ["%s"] * 5 + ["%s", "%s::jsonb", "%s::vector", "%s"] + ["%s"] * len(promote)
        vals_sql = ", ".join(placeholders)

        stmt = f"INSERT INTO {self._fq()} ({cols_sql}) VALUES ({vals_sql}) {upsert_sql}"

        rows = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            base_values = [
                f"{c.doc_id}::{c.seq_num}",
                c.doc_id,
                c.seq_num,
                c.original_content,
                c.embedded_content,
                self.backend.tags_literal(tags),
                self.backend.json_literal(c.metadata),
                self.backend.vector_literal(emb),
                self.cfg.source_tag,
            ]
            promote_values = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
            rows.append(tuple(base_values + promote_values))

        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.executemany(stmt, rows)
            if self.cfg.delete_orphans:
                cur.execute(
                    f"DELETE FROM {self._fq()} WHERE doc_id = %s AND seq_num >= %s",
                    (doc_id, len(chunks)),
                )
            conn.commit()

    def count_docs(self) -> int:
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(DISTINCT doc_id) FROM {self._fq()}")
            return cur.fetchone()[0]
```

Note: `self.cfg.database_name` references a renamed field. Tests will fail until Task 12 renames `TargetConfig.schema_name` → `database_name`. We accept temporarily-broken state across this task and Task 12; the existing PG tests gate the merge in Task 14.

- [ ] **Step 2: Run static check (the new module imports cleanly)**

```bash
uv run python -c "from chunkshop.sinks.pg import PgSink, _jsonb_path_get; print('ok')"
```

Expected: `ok` (will fail on `database_name` reference unless config is also updated; if so, mark this step as known-broken until Task 12 ships, but proceed). If the import fails *only* because of the rename, that's expected. If it fails for any other reason, fix before continuing.

- [ ] **Step 3: Commit (the new file; tests deferred until config rename lands)**

```bash
git add python/src/chunkshop/sinks/pg.py
git commit -m "feat(sinks/pg): port PgVectorSink to use PostgresBackend dialect

Will be wired up + tested green in Task 14 after config rename
(schema_name → database_name) lands."
```

---

### Task 9: Add `sinks/__init__.py` `load_sink` factory

**Files:**
- Modify: `python/src/chunkshop/sinks/__init__.py`
- Test: `python/tests/chunkshop/test_sinks_load.py`

- [ ] **Step 1: Write failing test**

```python
# python/tests/chunkshop/test_sinks_load.py
import os
import pytest
from unittest.mock import MagicMock

from chunkshop.sinks import load_sink


def test_load_sink_postgres(monkeypatch):
    monkeypatch.setenv("DUMMY_DSN", "postgresql://x:1/y")
    cfg = MagicMock(type="postgres", dsn_env="DUMMY_DSN")
    cfg.database_name = "x"
    cfg.table = "y"
    sink = load_sink(cfg, embed_dim=384)
    assert sink.__class__.__name__ == "PgSink"


def test_load_sink_unknown():
    cfg = MagicMock(type="oracle", dsn_env="X")
    with pytest.raises(ValueError, match="unknown target"):
        load_sink(cfg, embed_dim=384)
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/chunkshop/test_sinks_load.py -v
```

Expected: FAIL — `load_sink` not exported.

- [ ] **Step 3: Implement**

```python
# python/src/chunkshop/sinks/__init__.py
"""Sink layer: per-backend writers for chunkshop's chunks table."""
from chunkshop.backends import load_backend
from chunkshop.sinks.base import Sink
from chunkshop.sinks.pg import PgSink


def load_sink(cfg, embed_dim: int) -> Sink:
    """Factory: dispatch on cfg.type, attach matching Backend, return Sink."""
    if cfg.type == "postgres":
        backend = load_backend(name="postgres", dsn_env=cfg.dsn_env)
        return PgSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    # Future: "mariadb" → load MariaDBBackend + MariaDbSink (Phase 5)
    raise ValueError(f"unknown target type: {cfg.type!r}")


__all__ = ["Sink", "PgSink", "load_sink"]
```

- [ ] **Step 4: Defer test execution**

Same caveat as Task 8 — `cfg.database_name` and `cfg.type` won't exist until config is updated in Task 12. We commit the factory now and verify it green in Task 14.

```bash
uv run python -c "from chunkshop.sinks import load_sink, PgSink, Sink; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/sinks/__init__.py \
        python/tests/chunkshop/test_sinks_load.py
git commit -m "feat(sinks): load_sink factory dispatching on cfg.type"
```

---

## Phase 3 — Config rename + wiring + delete `sink.py`

### Task 10: Rename `TargetConfig.schema_name` → `database_name`; add `type` discriminator

**Files:**
- Modify: `python/src/chunkshop/config.py`
- Modify: `python/tests/chunkshop/test_config.py` (existing tests reference `schema_name`)
- Test: `python/tests/chunkshop/test_config_target_v4.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# python/tests/chunkshop/test_config_target_v4.py
import pytest
from chunkshop.config import TargetConfig


def test_target_type_postgres_with_database_alias():
    cfg = TargetConfig(
        type="postgres",
        dsn_env="PG_DSN",
        database="chunkshop",
        table="my_chunks",
        mode="overwrite",
    )
    assert cfg.type == "postgres"
    assert cfg.database_name == "chunkshop"  # internal name
    assert cfg.table == "my_chunks"


def test_target_rejects_unknown_type():
    with pytest.raises(Exception):
        TargetConfig(type="oracle", dsn_env="X", database="x", table="y", mode="overwrite")


def test_target_rejects_legacy_schema_field():
    # v4.0 breaks compat with the old `schema:` alias
    with pytest.raises(Exception):
        TargetConfig(type="postgres", dsn_env="X", schema="x", table="y", mode="overwrite")


def test_target_rejects_legacy_overwrite_field():
    # `overwrite: true` was deprecated in 0.3.x; v4.0 removes it entirely
    with pytest.raises(Exception):
        TargetConfig(type="postgres", dsn_env="X", database="x", table="y", overwrite=True)


def test_target_database_passes_ident_validator():
    with pytest.raises(Exception):
        TargetConfig(type="postgres", dsn_env="X", database="My-DB", table="y", mode="overwrite")
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/chunkshop/test_config_target_v4.py -v
```

Expected: 5 FAIL or import errors — `TargetConfig` doesn't have `type` yet, `schema:` is currently accepted, etc.

- [ ] **Step 3: Modify `config.py`**

Replace the existing `TargetConfig` class (currently at `python/src/chunkshop/config.py:487-514`) with:

```python
class TargetConfig(_Base):
    type: Literal["postgres"]   # discriminator; future: Literal["postgres", "mariadb", "clickhouse"]
    dsn_env: str
    database_name: str = Field(alias="database")
    table: str
    hnsw: bool = True
    mode: Literal["overwrite", "append", "create_if_missing"] = "overwrite"
    source_tag: Optional[str] = None
    promote_metadata: list[PromoteColumn] = Field(default_factory=list)
    force_overwrite: bool = False
    delete_orphans: bool = False

    @field_validator("table", "database_name", "source_tag")
    @classmethod
    def _safe_ident(cls, v):
        if v is None:
            return v
        if not re.match(r"^[a-z_][a-z0-9_]*$", v):
            raise ValueError(
                f"table/database/source_tag must match ^[a-z_][a-z0-9_]*$, got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _append_requires_source_tag(self):
        if self.mode == "append" and not self.source_tag:
            raise ValueError("source_tag is required when mode='append'")
        return self
```

Removed: legacy `overwrite: bool` field, `schema_name` (renamed). Added: `type` discriminator (currently single-valued `Literal["postgres"]`; expanded in Phase 5).

- [ ] **Step 4: Run new + existing config tests**

```bash
uv run pytest tests/chunkshop/test_config_target_v4.py tests/chunkshop/test_config.py tests/chunkshop/test_config_target_flexibility.py -v
```

Expected: new tests PASS (5/5). Existing tests: many FAIL because they pass `schema=` or use `cfg.schema_name`. Fix those tests next. **Do not skip this step** — read each failure and update the test to use `database=` and `database_name`.

- [ ] **Step 5: Update existing tests + commit**

In each failing test, replace:
- `schema=` → `database=` (in `TargetConfig(...)` calls)
- `cfg.schema_name` → `cfg.database_name` (in assertions)
- Add `type="postgres"` to every `TargetConfig(...)` instantiation that doesn't have it

Run again to confirm green:

```bash
uv run pytest tests/chunkshop/test_config.py tests/chunkshop/test_config_target_flexibility.py tests/chunkshop/test_config_target_v4.py -v
```

Then:

```bash
git add python/src/chunkshop/config.py python/tests/chunkshop/
git commit -m "feat(config): TargetConfig.type discriminator + schema→database rename

- Adds Literal[\"postgres\"] discriminator (more backends added later)
- Renames internal schema_name → database_name with alias=\"database\"
- Removes deprecated overwrite: bool legacy field
- Tests updated to new field names"
```

---

### Task 11: Rename `PgTableSource.schema_name` → `database_name`

**Files:**
- Modify: `python/src/chunkshop/config.py` (PgTableSource at lines 32-45)
- Modify: `python/src/chunkshop/sources/pg_table.py` (uses `schema_name`)
- Modify: tests that exercise pg_table

- [ ] **Step 1: Write failing test**

```python
# Append to python/tests/chunkshop/test_config_target_v4.py
def test_pg_table_source_database_alias():
    from chunkshop.config import PgTableSource
    s = PgTableSource(
        type="pg_table",
        dsn_env="PG",
        database="my_app",
        table="docs",
        id_column="id",
        content_column="body",
    )
    assert s.database_name == "my_app"
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/chunkshop/test_config_target_v4.py::test_pg_table_source_database_alias -v
```

Expected: FAIL.

- [ ] **Step 3: Modify `config.py` PgTableSource**

Replace `schema_name: str = Field(alias="schema")` with `database_name: str = Field(alias="database")`. Keep the rest of the model identical.

Modify `python/src/chunkshop/sources/pg_table.py` line 55 and line 56:

```python
# was: schema=sql.Identifier(self.cfg.schema_name),
schema=sql.Identifier(self.cfg.database_name),
```

- [ ] **Step 4: Run test + existing pg_table integration tests**

```bash
uv run pytest tests/chunkshop/test_config_target_v4.py::test_pg_table_source_database_alias \
              tests/chunkshop/test_metadata_promotion_e2e.py \
              tests/chunkshop/test_multi_source_ingest.py -v
```

Expected: new test PASS. Existing tests: any that pass `schema=` need updating to `database=`.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py \
        python/src/chunkshop/sources/pg_table.py \
        python/tests/chunkshop/
git commit -m "feat(config): PgTableSource schema→database rename"
```

---

### Task 12: Wire `runner.py` and `pipeline.py` to use `load_sink`

**Files:**
- Modify: `python/src/chunkshop/runner.py:15,73`
- Modify: `python/src/chunkshop/pipeline.py:32,61,110-126`

- [ ] **Step 1: Update `runner.py`**

Change line 15:

```python
# was: from chunkshop.sink import PgVectorSink
from chunkshop.sinks import load_sink
```

Change line 73:

```python
# was: sink = PgVectorSink(cfg.target, embed_dim=cfg.embedder.dim)
sink = load_sink(cfg.target, embed_dim=cfg.embedder.dim)
```

- [ ] **Step 2: Update `pipeline.py`**

Change line 32:

```python
# was: from chunkshop.sink import PgVectorSink
from chunkshop.sinks import load_sink
```

Change line 61:

```python
# was: self._sink = PgVectorSink(cfg.target, embed_dim=cfg.embedder.dim)
self._sink = load_sink(cfg.target, embed_dim=cfg.embedder.dim)
```

Update `delete_document` (current lines 103-127) to go through the backend rather than direct psycopg:

```python
def delete_document(self, doc_id: str) -> int:
    """Remove every chunk for a doc_id, scoped to this pipeline's source_tag."""
    cfg = self.cfg.target
    fq = self._sink._fq()  # PgSink exposes the formatted FQN
    backend = self._sink.backend
    with backend.connect() as conn, conn.cursor() as cur:
        if cfg.source_tag:
            cur.execute(
                f"DELETE FROM {fq} WHERE doc_id = %s AND source = %s",
                (doc_id, cfg.source_tag),
            )
        else:
            cur.execute(f"DELETE FROM {fq} WHERE doc_id = %s", (doc_id,))
        deleted = cur.rowcount
        conn.commit()
    return deleted
```

Remove the now-unused imports `from psycopg import sql` and `import psycopg` from the top of `pipeline.py`.

- [ ] **Step 3: Run runner + pipeline-adjacent tests**

```bash
uv run pytest tests/chunkshop/test_runner.py tests/chunkshop/test_runner_framer.py -v
```

Expected: PASS (config tests + import path resolution).

- [ ] **Step 4: Commit**

```bash
git add python/src/chunkshop/runner.py python/src/chunkshop/pipeline.py
git commit -m "feat(runner,pipeline): use load_sink + backend connect (drops sink.py import)"
```

---

### Task 13: Update sample YAMLs and bakeoff configs to v4 shape

**Files:**
- Modify: every YAML matching `schema:` or missing `target.type:`

The grep from pre-flight showed these as needing updates:
- `docs/samples/sample.yaml`, `sample-sentence-aware.yaml`, `sample-multi-source.yaml`, `sample-summary-embed.yaml`, `sample-hierarchical.yaml`, `sample-semantic.yaml`, `bakeoff.yaml`
- `docs/samples/incremental-pg-table/sample.yaml`, `demo.yaml`
- `docs/samples/sales-crm/from-pg-table.yaml`
- `docs/samples/bakeoff-ntsb/bakeoff-ntsb.yaml`
- `docs/samples/if-oversize/with-fallback.yaml`
- `python/src/chunkshop/configs/example-files-to-bge.yaml`
- `python/src/chunkshop/configs/factorial/{A,B,C,D}-{bge-small,bge-base,nomic}.yaml`
- `python/src/chunkshop/configs/factorial-int8/{A,B,C,D}-{bge-small,bge-base,nomic}.yaml`

- [ ] **Step 1: For each YAML, apply two find/replace operations**

In `target:` blocks:
- Add `type: postgres` as the first line under `target:`
- Rename `schema: <value>` → `database: <value>`

In `source:` blocks where `type: pg_table`:
- Rename `schema: <value>` → `database: <value>`

Example diff for `docs/samples/sample.yaml`:

```yaml
# was:
target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_samples
  table: handbook

# v4:
target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: chunkshop_samples
  table: handbook
```

A reliable command-line approach (verify each file diff before committing):

```bash
cd /home/yonk/yonk-tools/chunkshop-v4
for f in $(grep -rln 'schema:' docs/samples/ python/src/chunkshop/configs/); do
  # Visually diff each file before applying. Use sed only if the diff is clean.
  echo "=== $f ==="
  grep -nE 'schema:|^target:|type: pg_table' "$f"
done
```

Apply edits via your editor — sed is risky with YAML. After each file:

```bash
git diff -- "$f"
```

- [ ] **Step 2: Test that all sample YAMLs load without validation errors**

```bash
cd /home/yonk/yonk-tools/chunkshop-v4/python
uv run python -c "
import sys
from pathlib import Path
from chunkshop.config import load_config
errs = []
for p in Path('../docs/samples').rglob('*.yaml'):
    if p.name == 'README.md': continue
    try:
        load_config(p)
    except Exception as e:
        errs.append(f'{p}: {e}')
for p in Path('src/chunkshop/configs').rglob('*.yaml'):
    try:
        load_config(p)
    except Exception as e:
        errs.append(f'{p}: {e}')
if errs:
    print('\\n'.join(errs))
    sys.exit(1)
print('all configs valid')
"
```

Expected: `all configs valid`.

- [ ] **Step 3: Commit**

```bash
git add docs/samples/ python/src/chunkshop/configs/
git commit -m "feat(yaml): migrate samples + bakeoff configs to v4 shape

- Add target.type: postgres to every target block
- Rename target.schema → target.database
- Rename source.schema → source.database for pg_table sources"
```

---

### Task 14: Delete `sink.py`; run full PG suite — gate SC-001, SC-002

**Files:**
- Delete: `python/src/chunkshop/sink.py`

- [ ] **Step 1: Verify no remaining imports of `chunkshop.sink`**

```bash
cd /home/yonk/yonk-tools/chunkshop-v4
grep -rn "from chunkshop.sink import\|chunkshop\.sink\." python/ 2>/dev/null
```

Expected: no matches. If any remain (e.g. in tests still using `PgVectorSink`), update them to `from chunkshop.sinks import load_sink` or `from chunkshop.sinks.pg import PgSink`.

- [ ] **Step 2: Delete the file**

```bash
git rm python/src/chunkshop/sink.py
```

- [ ] **Step 3: Run the full test suite**

```bash
cd /home/yonk/yonk-tools/chunkshop-v4/python
uv run pytest -q 2>&1 | tail -30
```

Expected: same passing count as Task 0 baseline. Failing tests at this point indicate either a missed YAML migration (Task 13) or a missed test update (Tasks 10–11). Fix in place — do **not** skip.

- [ ] **Step 4: Run the bakeoff smoke (SC-001 gate)**

If `$CHUNKSHOP_TEST_DSN` is reachable:

```bash
uv run pytest tests/chunkshop/test_bakeoff_e2e.py -v
```

Expected: bakeoff tests pass.

- [ ] **Step 5: Commit + DC-2 checkpoint**

```bash
git commit -m "refactor: remove sink.py — replaced by sinks/pg.py + backends/postgres.py

DC-2 (drift checkpoint): existing PG behavior preserved through the new
abstraction. SC-001 (bakeoff parity), SC-002 (existing tests pass) green."
```

---

## Phase 4 — `backends/sqlite.py`

### Task 15: Add `[sqlite]` extra; `backends/sqlite.py` — connect (with sqlite-vec extension + WAL) + identifier safety

**Files:**
- Modify: `python/pyproject.toml`
- Create: `python/src/chunkshop/backends/sqlite.py`
- Test: `python/tests/chunkshop/test_backend_sqlite.py`

- [ ] **Step 1: Add the optional dep**

In `python/pyproject.toml` under `[project.optional-dependencies]`, add:

```toml
sqlite = ["sqlite-vec>=0.1.6"]
```

Update the `all-backends` aggregate to include it:

```toml
all-backends = ["chunkshop[sqlite,mariadb,clickhouse]"]
```

(`clickhouse` extra doesn't exist yet — add it as an empty placeholder or remove from this aggregate. Cleanest: drop CH from `all-backends` until CH lands.)

Install:

```bash
cd /home/yonk/yonk-tools/chunkshop-v4/python
uv sync --extra dev --extra extractors --extra sqlite
```

- [ ] **Step 2: Write failing tests**

```python
# python/tests/chunkshop/test_backend_sqlite.py
import pytest
import sqlite3

pytest.importorskip("sqlite_vec")

from chunkshop.backends.sqlite import SQLiteBackend


@pytest.fixture
def be(monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", ":memory:")
    return SQLiteBackend(dsn_env="SQLITE_PATH")


def test_name_and_supports_upsert(be):
    assert be.name == "sqlite"
    assert be.supports_upsert is True


def test_quote_ident_uses_double_quotes(be):
    assert be.quote_ident("my_table") == '"my_table"'


def test_quote_ident_escapes_embedded_double_quote(be):
    assert be.quote_ident('weird"name') == '"weird""name"'


def test_fq_table_ignores_db_prefix(be):
    # SQLite has no schema concept — the database value from YAML is ignored;
    # fq returns just the bare table identifier.
    assert be.fq_table("anything", "chunks") == '"chunks"'


def test_connect_loads_vec_extension_and_enables_wal(be):
    with be.connect() as conn:
        cur = conn.cursor()
        # vec_version() is provided by sqlite-vec when its extension is loaded
        cur.execute("SELECT vec_version()")
        v = cur.fetchone()[0]
        assert v.startswith("v") or v[0].isdigit()
        # journal_mode: in-memory DB returns "memory" (PRAGMA WAL is for file DBs);
        # accept either WAL or memory
        cur.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0].lower()
        assert mode in {"wal", "memory"}
```

- [ ] **Step 3: Run failing**

```bash
uv run pytest tests/chunkshop/test_backend_sqlite.py -v
```

Expected: ModuleNotFoundError or AttributeError.

- [ ] **Step 4: Implement skeleton**

```python
# python/src/chunkshop/backends/sqlite.py
"""SQLite backend (with sqlite-vec extension for vector storage).

SQLite has no schema/database namespace concept — chunkshop's YAML `database`
field is required by config (loose parity) but ignored at runtime. The DSN env
var holds the file path or `:memory:`.
"""
from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Literal


class SQLiteBackend:
    """Backend Protocol implementation for SQLite + sqlite-vec."""

    name: Literal["sqlite"] = "sqlite"
    supports_upsert: bool = True

    def __init__(self, dsn_env: str):
        self._dsn_env = dsn_env

    @contextmanager
    def connect(self) -> Iterator[Any]:
        path = os.environ[self._dsn_env]
        conn = sqlite3.connect(path)
        # Enable extension loading then load sqlite-vec.
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        # WAL gives non-blocking reads during writes; no-op for :memory:.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        try:
            yield conn
        finally:
            conn.close()

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def fq_table(self, db: str, table: str) -> str:
        # SQLite has no schema concept — `db` is ignored at runtime.
        del db
        return self.quote_ident(table)
```

- [ ] **Step 5: Run + commit**

```bash
uv run pytest tests/chunkshop/test_backend_sqlite.py -v
```

Expected: 5 PASS.

```bash
git add python/pyproject.toml \
        python/src/chunkshop/backends/sqlite.py \
        python/tests/chunkshop/test_backend_sqlite.py
git commit -m "feat(backends/sqlite): connect (vec ext + WAL) + identifier safety + [sqlite] extra"
```

---

### Task 16: `backends/sqlite.py` — type DDL fragments, literals, json_path, DDL primitives, upsert

**Files:**
- Modify: `python/src/chunkshop/backends/sqlite.py`
- Modify: `python/tests/chunkshop/test_backend_sqlite.py`

- [ ] **Step 1: Append failing tests**

```python
# Append to python/tests/chunkshop/test_backend_sqlite.py
import json
import numpy as np


def test_vector_type_ddl(be):
    # SQLite vec0 virtual tables use FLOAT[N]
    assert be.vector_type_ddl(384) == "FLOAT[384]"


def test_json_type_ddl(be):
    # SQLite stores JSON as TEXT (advisory; sqlite is dynamically typed)
    assert be.json_type_ddl() == "TEXT"


def test_tags_array_type_ddl(be):
    assert be.tags_array_type_ddl() == "TEXT"


def test_text_pk_type_ddl(be):
    assert be.text_pk_type_ddl() == "TEXT"


def test_timestamp_now_default_ddl(be):
    out = be.timestamp_now_default_ddl()
    assert "CURRENT_TIMESTAMP" in out.upper()


def test_vector_literal_returns_json_array(be):
    arr = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = be.vector_literal(arr)
    # sqlite-vec accepts a JSON array as the bound parameter
    parsed = json.loads(out)
    assert len(parsed) == 3
    assert abs(parsed[0] - 0.1) < 1e-5


def test_tags_literal_serializes_to_json(be):
    assert json.loads(be.tags_literal(["a", "b"])) == ["a", "b"]


def test_json_literal_serializes(be):
    assert json.loads(be.json_literal({"a": 1})) == {"a": 1}


def test_json_path_simple(be):
    assert be.json_path_sql("metadata", "lang") == "json_extract(metadata,'$.lang')"


def test_json_path_nested(be):
    assert be.json_path_sql("metadata", "entities.ORG") == "json_extract(metadata,'$.entities.ORG')"


def test_create_database_sql_is_noop_comment(be):
    out = be.create_database_sql("anything")
    # SQLite has no schema concept; return a benign no-op (a SELECT or an empty-ish statement).
    # We use a "SELECT 1" as a portable no-op the executor can run safely.
    assert "SELECT 1" in out or out.strip() == ""


def test_drop_table_sql(be):
    assert be.drop_table_sql('"chunks"') == 'DROP TABLE "chunks"'


def test_add_column_if_not_exists_sql(be):
    out = be.add_column_if_not_exists_sql('"chunks"', "newcol", "TEXT")
    assert "ALTER TABLE" in out
    # SQLite: "ALTER TABLE x ADD COLUMN y TYPE" doesn't support IF NOT EXISTS until 3.35+.
    # Plan accepts SQLite >=3.35; if older, the Sink catches "duplicate column" errors.
    assert '"newcol"' in out


def test_upsert_clause_pg_compat(be):
    out = be.upsert_clause(["id"], ["content", "metadata"])
    assert "ON CONFLICT" in out and '("id")' in out
    assert 'DO UPDATE SET' in out
    assert '"content" = excluded."content"' in out
    assert '"metadata" = excluded."metadata"' in out


def test_upsert_clause_empty_update_cols(be):
    out = be.upsert_clause(["id"], [])
    assert "DO NOTHING" in out
```

- [ ] **Step 2: Run + verify failure**

```bash
uv run pytest tests/chunkshop/test_backend_sqlite.py -v
```

Expected: many FAIL with `AttributeError`.

- [ ] **Step 3: Append implementation to `SQLiteBackend` class**

```python
# Append inside SQLiteBackend class:
import json
import numpy as np
from typing import Any


    # Type DDL fragments
    def vector_type_ddl(self, dim: int) -> str:
        return f"FLOAT[{dim}]"

    def json_type_ddl(self) -> str:
        return "TEXT"

    def tags_array_type_ddl(self) -> str:
        return "TEXT"

    def text_pk_type_ddl(self) -> str:
        return "TEXT"

    def timestamp_now_default_ddl(self) -> str:
        return "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"

    # Value literals
    def vector_literal(self, arr: np.ndarray) -> str:
        # sqlite-vec accepts a JSON array as the bound parameter for the
        # virtual table's vector column.
        return json.dumps([float(x) for x in arr])

    def tags_literal(self, tags: list[str]) -> str:
        return json.dumps(list(tags))

    def json_literal(self, obj: Any) -> str:
        return json.dumps(obj)

    # JSON path extraction
    def json_path_sql(self, col_expr: str, dotted_path: str) -> str:
        return f"json_extract({col_expr},'$.{dotted_path}')"

    # DDL primitives
    def create_database_sql(self, name: str) -> str:
        # No-op on SQLite; return a benign statement so the runner can execute it.
        del name
        return "SELECT 1 -- chunkshop: SQLite has no database/schema concept"

    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str:
        # SQLite >=3.35 supports ADD COLUMN IF NOT EXISTS via the ALTER TABLE syntax.
        # If the deployed SQLite is older, the Sink catches "duplicate column name" errors.
        return f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {self.quote_ident(col)} {type_ddl}"

    def drop_table_sql(self, fq: str) -> str:
        return f"DROP TABLE {fq}"

    # Upsert / conflict handling — PG-compatible syntax
    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str:
        keys = ", ".join(self.quote_ident(c) for c in key_cols)
        if not update_cols:
            return f"ON CONFLICT ({keys}) DO NOTHING"
        sets = ", ".join(
            f"{self.quote_ident(c)} = excluded.{self.quote_ident(c)}" for c in update_cols
        )
        return f"ON CONFLICT ({keys}) DO UPDATE SET {sets}"
```

- [ ] **Step 4: Run + verify pass**

```bash
uv run pytest tests/chunkshop/test_backend_sqlite.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/backends/sqlite.py \
        python/tests/chunkshop/test_backend_sqlite.py
git commit -m "feat(backends/sqlite): type DDL + literals + json_path + DDL primitives + upsert"
```

---

### Task 17: `backends/sqlite.py` — `emit_chunks_table_ddl` (two-table layout) + introspection + lock no-op + register

**Files:**
- Modify: `python/src/chunkshop/backends/sqlite.py`
- Modify: `python/src/chunkshop/backends/__init__.py`
- Modify: `python/tests/chunkshop/test_backend_sqlite.py`

- [ ] **Step 1: Append failing tests**

```python
# Append to python/tests/chunkshop/test_backend_sqlite.py
from chunkshop.backends.base import ColSpec


def _canonical_cols_with_embedding():
    return [
        ColSpec("id", "TEXT", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "TEXT", nullable=False),
        ColSpec("seq_num", "INTEGER", nullable=False),
        ColSpec("original_content", "TEXT", nullable=False),
        ColSpec("embedded_content", "TEXT", nullable=False),
        ColSpec("tags", "TEXT", nullable=False, default="'[]'"),
        ColSpec("metadata", "TEXT", nullable=False, default="'{}'"),
        ColSpec("embedding", "FLOAT[384]", nullable=False),
        ColSpec("source", "TEXT"),
        ColSpec("created_at", "TEXT", nullable=False, default="CURRENT_TIMESTAMP"),
    ]


def test_emit_chunks_table_ddl_two_tables(be):
    cols = _canonical_cols_with_embedding()
    out = be.emit_chunks_table_ddl(fq='"chunks"', cols=cols, hnsw=False, dim=384)
    # Three statements: regular CREATE TABLE, doc_seq index, vec0 virtual table
    assert len(out) == 3
    create_main = out[0]
    assert "CREATE TABLE IF NOT EXISTS" in create_main
    # Embedding column must NOT appear in the main table (it lives in the vec0 virtual table)
    assert '"embedding"' not in create_main
    assert '"id" TEXT' in create_main
    assert "PRIMARY KEY" in create_main
    create_idx = out[1]
    assert "CREATE INDEX IF NOT EXISTS" in create_idx and "doc_id" in create_idx and "seq_num" in create_idx
    create_vec = out[2]
    assert "CREATE VIRTUAL TABLE IF NOT EXISTS" in create_vec
    assert "USING vec0" in create_vec
    assert "FLOAT[384]" in create_vec


def test_emit_chunks_table_ddl_hnsw_true_logs_no_special_index(be):
    # On SQLite, hnsw=True is a no-op (logged warning happens at Sink layer).
    cols = _canonical_cols_with_embedding()
    out = be.emit_chunks_table_ddl(fq='"chunks"', cols=cols, hnsw=True, dim=384)
    # Same three statements; no additional HNSW DDL
    assert len(out) == 3


def test_with_create_lock_is_noop(be):
    with be.connect() as conn:
        cur = conn.cursor()
        with be.with_create_lock(cur, "anything"):
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1


def test_table_exists(be):
    with be.connect() as conn:
        cur = conn.cursor()
        assert be.table_exists(cur, "ignored", "no_such_table") is False
        cur.execute('CREATE TABLE "real_one" (id TEXT)')
        assert be.table_exists(cur, "ignored", "real_one") is True


def test_embedding_dim_introspection(be):
    with be.connect() as conn:
        cur = conn.cursor()
        # Create the two-table pair via the backend's DDL emission
        cols = _canonical_cols_with_embedding()
        for stmt in be.emit_chunks_table_ddl(fq='"chunks"', cols=cols, hnsw=False, dim=384):
            if stmt.strip().startswith("CREATE"):
                cur.execute(stmt)
        assert be.embedding_dim(cur, "ignored", "chunks") == 384
        assert be.embedding_dim(cur, "ignored", "no_such") is None
```

- [ ] **Step 2: Run + verify failure**

```bash
uv run pytest tests/chunkshop/test_backend_sqlite.py -v
```

Expected: AttributeError on the new methods.

- [ ] **Step 3: Implement**

```python
# Append inside SQLiteBackend class:
import re
from contextlib import contextmanager


    # Composite DDL — two-table layout with vec0 virtual table for the embedding
    def emit_chunks_table_ddl(
        self, fq: str, cols: list, hnsw: bool, dim: int, engine: str | None = None,
    ) -> list[str]:
        del engine  # SQLite has no engine clause
        # Find and split out the embedding column — it lives only in the vec0 table.
        main_cols = [c for c in cols if c.name != "embedding"]
        embed_cols = [c for c in cols if c.name == "embedding"]
        if not embed_cols:
            raise ValueError("emit_chunks_table_ddl on SQLite expects an 'embedding' ColSpec")

        col_lines = []
        pk_cols = []
        for c in main_cols:
            line = f"  {self.quote_ident(c.name)} {c.type_ddl}"
            if c.default is not None:
                line += f" DEFAULT {c.default}"
            if not c.nullable:
                line += " NOT NULL"
            col_lines.append(line)
            if c.is_primary_key:
                pk_cols.append(c.name)
        lines = ",\n".join(col_lines)
        if pk_cols:
            pk = ", ".join(self.quote_ident(c) for c in pk_cols)
            lines += f",\n  PRIMARY KEY ({pk})"
        create_main = f"CREATE TABLE IF NOT EXISTS {fq} (\n{lines}\n)"

        # Strip quotes for index naming
        bare = fq.strip('"')
        create_idx = (
            f'CREATE INDEX IF NOT EXISTS {self.quote_ident(bare + "_doc_seq_idx")} '
            f'ON {fq} ("doc_id", "seq_num")'
        )

        # vec0 virtual table — id is the join key with the main chunks table
        vec_fq = self.quote_ident(bare + "_vec")
        create_vec = (
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {vec_fq} USING vec0("
            f'id TEXT PRIMARY KEY, embedding FLOAT[{dim}]'
            f")"
        )
        # hnsw flag is intentionally ignored here — the Sink emits a one-time warning.
        del hnsw
        return [create_main, create_idx, create_vec]

    # Introspection
    def table_exists(self, cur: Any, db: str, table: str) -> bool:
        del db
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual table') AND name=?",
            (table,),
        )
        return cur.fetchone() is not None

    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None:
        del db
        # vec0 virtual tables store their declared schema in sqlite_master.sql.
        cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table + "_vec",),
        )
        r = cur.fetchone()
        if not r or not r[0]:
            return None
        m = re.search(r"FLOAT\[(\d+)\]", r[0], re.IGNORECASE)
        return int(m.group(1)) if m else None

    # Concurrent-create lock — no-op (SQLite serializes writers via file lock)
    @contextmanager
    def with_create_lock(self, cur: Any, key: str) -> Iterator[None]:
        del cur, key
        yield
```

Update `python/src/chunkshop/backends/__init__.py`:

```python
"""Backend layer: connection lifecycle + dialect helpers per database backend."""
from chunkshop.backends.base import Backend, ColSpec
from chunkshop.backends.postgres import PostgresBackend


def load_backend(name: str, dsn_env: str) -> Backend:
    if name == "postgres":
        return PostgresBackend(dsn_env=dsn_env)
    if name == "sqlite":
        from chunkshop.backends.sqlite import SQLiteBackend
        return SQLiteBackend(dsn_env=dsn_env)
    if name == "mariadb":
        from chunkshop.backends.mariadb import MariaDBBackend
        return MariaDBBackend(dsn_env=dsn_env)
    raise ValueError(f"unknown backend: {name!r}")


__all__ = ["Backend", "ColSpec", "PostgresBackend", "load_backend"]
```

Append to `python/tests/chunkshop/test_backends_load.py`:

```python
def test_load_backend_sqlite(monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", ":memory:")
    from chunkshop.backends import load_backend
    be = load_backend(name="sqlite", dsn_env="SQLITE_PATH")
    assert be.name == "sqlite"
```

- [ ] **Step 4: Run + verify pass**

```bash
uv run pytest tests/chunkshop/test_backend_sqlite.py tests/chunkshop/test_backends_load.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/backends/sqlite.py \
        python/src/chunkshop/backends/__init__.py \
        python/tests/chunkshop/test_backend_sqlite.py \
        python/tests/chunkshop/test_backends_load.py
git commit -m "feat(backends/sqlite): emit_chunks_table_ddl two-table layout + introspection + load"
```

---

## Phase 5 — `sinks/sqlite.py` + `sources/sqlite_table.py` + tests

### Task 18: Add `sqlite` to `TargetConfig.type`; create `sinks/sqlite.py` with two-table write

**Files:**
- Modify: `python/src/chunkshop/config.py`
- Create: `python/src/chunkshop/sinks/sqlite.py`
- Modify: `python/src/chunkshop/sinks/__init__.py`

- [ ] **Step 1: Expand `TargetConfig.type` literal**

```python
# in python/src/chunkshop/config.py:
# was: type: Literal["postgres"]   (set in Task 10)
type: Literal["postgres", "sqlite"]   # MariaDB added in Task 24
```

- [ ] **Step 2: Create `sinks/sqlite.py`**

```python
# python/src/chunkshop/sinks/sqlite.py
"""SQLite sink — chunks-table writer with two-table layout (chunks + chunks_vec).

The embedding column lives in a `vec0` virtual table joined on `id`. The Sink
owns the two-table dance: every `write_document` writes both atomically; every
`delete_orphans` deletes from both.
"""
from __future__ import annotations
import logging
import sqlite3
from typing import Any

import numpy as np

from chunkshop.backends.base import ColSpec
from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig

_log = logging.getLogger(__name__)
_HNSW_WARNED: set[int] = set()  # process-id keyed so we warn once per process


def _jsonb_path_get(meta: dict, path: str):
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _canonical_cols(dim: int) -> list[ColSpec]:
    """Canonical columns INCLUDING embedding — the backend splits it out for the vec0 table."""
    return [
        ColSpec("id", "TEXT", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "TEXT", nullable=False),
        ColSpec("seq_num", "INTEGER", nullable=False),
        ColSpec("original_content", "TEXT", nullable=False),
        ColSpec("embedded_content", "TEXT", nullable=False),
        ColSpec("tags", "TEXT", nullable=False, default="'[]'"),
        ColSpec("metadata", "TEXT", nullable=False, default="'{}'"),
        ColSpec("embedding", f"FLOAT[{dim}]", nullable=False),
        ColSpec("source", "TEXT"),
        ColSpec("created_at", "TEXT", nullable=False, default="CURRENT_TIMESTAMP"),
    ]


_SQLITE_TYPE = {
    "text": "TEXT", "text[]": "TEXT", "int": "INTEGER", "bigint": "INTEGER",
    "boolean": "INTEGER", "jsonb": "TEXT", "timestamptz": "TEXT", "date": "TEXT",
}


def _pg_type_to_sqlite(pg_type: str) -> str:
    return _SQLITE_TYPE.get(pg_type, pg_type)


class SqliteSink:
    """Per-document writer to a SQLite chunks-table pair."""

    def __init__(self, cfg: TargetConfig, backend: SQLiteBackend, embed_dim: int):
        self.cfg = cfg
        self.backend = backend
        self.embed_dim = embed_dim
        if cfg.hnsw:
            import os
            pid = os.getpid()
            if pid not in _HNSW_WARNED:
                _HNSW_WARNED.add(pid)
                _log.warning(
                    "target.hnsw=true on SQLite is a no-op — sqlite-vec uses brute-force KNN. "
                    "Querying with `embedding MATCH '[…]' AND k = N` works without an index."
                )

    def _fq_main(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table)

    def _fq_vec(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table + "_vec")

    def create_table(self) -> None:
        with self.backend.connect() as conn:
            cur = conn.cursor()
            with self.backend.with_create_lock(cur, self.cfg.database_name):
                # SELECT 1 noop on SQLite — emit anyway for symmetry
                cur.execute(self.backend.create_database_sql(self.cfg.database_name))
                if self.cfg.mode == "overwrite":
                    self._overwrite_create(cur)
                elif self.cfg.mode == "append":
                    self._append_preflight(cur)
                elif self.cfg.mode == "create_if_missing":
                    self._create_if_missing(cur)
                else:
                    raise ValueError(f"unknown mode: {self.cfg.mode}")
            conn.commit()

    def _create_base_ddl(self, cur) -> None:
        for stmt in self.backend.emit_chunks_table_ddl(
            fq=self._fq_main(),
            cols=_canonical_cols(self.embed_dim),
            hnsw=self.cfg.hnsw,
            dim=self.embed_dim,
        ):
            cur.execute(stmt)
        self._ensure_promote_columns(cur)

    def _ensure_promote_columns(self, cur) -> None:
        for pc in self.cfg.promote_metadata:
            try:
                cur.execute(self.backend.add_column_if_not_exists_sql(
                    self._fq_main(), pc.column_name, _pg_type_to_sqlite(pc.type),
                ))
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    continue
                raise

    def _overwrite_create(self, cur) -> None:
        if self._table_exists(cur) and not self.cfg.force_overwrite:
            cur.execute(f"SELECT DISTINCT source FROM {self._fq_main()} WHERE source IS NOT NULL LIMIT 10")
            existing = {r[0] for r in cur.fetchall()}
            my_tag = self.cfg.source_tag
            foreign = existing - ({my_tag} if my_tag else set())
            if foreign:
                raise RuntimeError(
                    f"overwrite refuses to drop {self.cfg.table}: foreign source_tag {sorted(foreign)!r}"
                )
        if self._table_exists(cur):
            cur.execute(self.backend.drop_table_sql(self._fq_main()))
            cur.execute(f"DROP TABLE IF EXISTS {self._fq_vec()}")
        self._create_base_ddl(cur)

    def _create_if_missing(self, cur) -> None:
        if not self._table_exists(cur):
            self._create_base_ddl(cur)
        else:
            try:
                cur.execute(self.backend.add_column_if_not_exists_sql(self._fq_main(), "source", "TEXT"))
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
            self._ensure_promote_columns(cur)

    def _append_preflight(self, cur) -> None:
        if not self._table_exists(cur):
            raise RuntimeError(f"append mode: table {self.cfg.table} does not exist")
        current_dim = self.backend.embedding_dim(cur, self.cfg.database_name, self.cfg.table)
        if current_dim is None:
            raise RuntimeError(f"append mode: {self.cfg.table} has no vec0 partner table")
        if current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target dim {current_dim} != cell embed_dim {self.embed_dim}"
            )
        try:
            cur.execute(self.backend.add_column_if_not_exists_sql(self._fq_main(), "source", "TEXT"))
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        self._ensure_promote_columns(cur)

    def _table_exists(self, cur) -> bool:
        return self.backend.table_exists(cur, self.cfg.database_name, self.cfg.table)

    def write_document(
        self, doc_id: str, chunks: list[Chunk], embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None:
        if len(chunks) != len(embeddings) or len(chunks) != len(tags_per_chunk):
            raise ValueError("chunks/embeddings/tags length mismatch")

        promote = self.cfg.promote_metadata
        # Main table cols (no embedding)
        main_col_names = [
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "source",
        ] + [pc.column_name for pc in promote]
        update_cols = ["original_content", "embedded_content", "tags", "metadata"] + [pc.column_name for pc in promote]
        # `source` excluded from update — write-once
        upsert = self.backend.upsert_clause(["id"], update_cols)
        cols_sql = ", ".join(self.backend.quote_ident(c) for c in main_col_names)
        placeholders = ", ".join(["?"] * len(main_col_names))
        main_stmt = f"INSERT INTO {self._fq_main()} ({cols_sql}) VALUES ({placeholders}) {upsert}"

        # Vec table — id + embedding
        vec_stmt = (
            f"INSERT INTO {self._fq_vec()} (id, embedding) VALUES (?, ?) "
            f"ON CONFLICT(id) DO UPDATE SET embedding = excluded.embedding"
        )

        main_rows = []
        vec_rows = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            chunk_id = f"{c.doc_id}::{c.seq_num}"
            base = [
                chunk_id, c.doc_id, c.seq_num, c.original_content, c.embedded_content,
                self.backend.tags_literal(tags),
                self.backend.json_literal(c.metadata),
                self.cfg.source_tag,
            ]
            promoted = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
            main_rows.append(tuple(base + promoted))
            vec_rows.append((chunk_id, self.backend.vector_literal(emb)))

        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.executemany(main_stmt, main_rows)
            cur.executemany(vec_stmt, vec_rows)
            if self.cfg.delete_orphans:
                cur.execute(
                    f"DELETE FROM {self._fq_main()} WHERE doc_id = ? AND seq_num >= ?",
                    (doc_id, len(chunks)),
                )
                cur.execute(
                    f"DELETE FROM {self._fq_vec()} WHERE id LIKE ? || '::%' "
                    f"AND CAST(substr(id, instr(id, '::') + 2) AS INTEGER) >= ?",
                    (doc_id, len(chunks)),
                )
            conn.commit()

    def count_docs(self) -> int:
        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(DISTINCT doc_id) FROM {self._fq_main()}")
            return cur.fetchone()[0]
```

- [ ] **Step 3: Register in `load_sink`**

```python
# python/src/chunkshop/sinks/__init__.py — modify:
def load_sink(cfg, embed_dim: int):
    if cfg.type == "postgres":
        backend = load_backend(name="postgres", dsn_env=cfg.dsn_env)
        return PgSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "sqlite":
        from chunkshop.sinks.sqlite import SqliteSink
        backend = load_backend(name="sqlite", dsn_env=cfg.dsn_env)
        return SqliteSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "mariadb":
        from chunkshop.sinks.mariadb import MariaDbSink
        backend = load_backend(name="mariadb", dsn_env=cfg.dsn_env)
        return MariaDbSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    raise ValueError(f"unknown target type: {cfg.type!r}")
```

- [ ] **Step 4: Verify import**

```bash
uv run python -c "from chunkshop.sinks.sqlite import SqliteSink; from chunkshop.sinks import load_sink; print('ok')"
```

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py \
        python/src/chunkshop/sinks/sqlite.py \
        python/src/chunkshop/sinks/__init__.py
git commit -m "feat(sinks/sqlite): SqliteSink — two-table dance + HNSW warning + load_sink dispatch"
```

---

### Task 19: `sources/sqlite_table.py` + register

**Files:**
- Modify: `python/src/chunkshop/config.py` (add SqliteTableSource)
- Create: `python/src/chunkshop/sources/sqlite_table.py`
- Modify: `python/src/chunkshop/sources/__init__.py`

- [ ] **Step 1: Add config class**

In `python/src/chunkshop/config.py`, after the `PgTableSource` class (MariaDB source is added later, in Task 28):

```python
class SqliteTableSource(_Base):
    type: Literal["sqlite_table"]
    dsn_env: str
    database_name: str = Field(alias="database")   # ignored at runtime; loose parity
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)
```

Update `SourceConfig` union to include it (don't add `MariaDbTableSource` yet — that lands in Task 28):

```python
SourceConfig = Annotated[
    Union[FilesSource, JsonCorpusSource, PgTableSource, SqliteTableSource,
          HttpSource, S3Source, InlineSource],
    Field(discriminator="type"),
]
```

- [ ] **Step 2: Create the source module**

```python
# python/src/chunkshop/sources/sqlite_table.py
from __future__ import annotations
from typing import Any, Iterator

from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.config import SqliteTableSource as Cfg
from chunkshop.sources.base import Document


class SqliteTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = SQLiteBackend(dsn_env=cfg.dsn_env)

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
            query += f" WHERE {self.cfg.where}"

        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.execute(query)
            for row in cur:
                metadata = {
                    self.cfg.metadata_columns[i]: row[meta_start + i]
                    for i in range(len(self.cfg.metadata_columns))
                }
                yield Document(
                    id=str(row[0]),
                    content=row[1],
                    title=row[title_idx] if title_idx is not None else None,
                    metadata=metadata if metadata else None,
                )
```

- [ ] **Step 3: Register in `sources/__init__.py`**

Read the existing `sources/__init__.py` and add a branch:

```python
if isinstance(cfg, SqliteTableSource):
    from chunkshop.sources.sqlite_table import SqliteTableSource as Impl
    return Impl(cfg)
```

- [ ] **Step 4: Smoke-test imports**

```bash
uv run python -c "
from chunkshop.sources.sqlite_table import SqliteTableSource
from chunkshop.config import SqliteTableSource as Cfg
print('ok')
"
```

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/config.py \
        python/src/chunkshop/sources/sqlite_table.py \
        python/src/chunkshop/sources/__init__.py
git commit -m "feat(sources/sqlite_table): SqliteTableSource + SqliteTableSource config"
```

---

### Task 20: SQLite integration tests + `sample-sqlite.yaml` — DC-3 checkpoint (gate SC-003, SC-004, SC-008..SC-013, SC-015..SC-017)

**Files:**
- Create: `python/tests/chunkshop/test_sink_sqlite.py`
- Create: `python/tests/chunkshop/test_source_sqlite.py`
- Create: `docs/samples/sample-sqlite.yaml`

- [ ] **Step 1: Write SQLite sink integration tests**

```python
# python/tests/chunkshop/test_sink_sqlite.py
"""Integration tests for SqliteSink. No external infrastructure — uses :memory:.

Covers SC-003 (basic ingest), SC-008 (append preflight), SC-009 (overwrite safety),
SC-010 (delete_orphans on both tables), SC-011 (HNSW no-op + caplog), SC-012
(promote_metadata via json_extract), SC-013 (identifier validation), SC-016 (HNSW warning).
"""
import logging
import os
import numpy as np
import pytest

pytest.importorskip("sqlite_vec")

from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig, PromoteColumn
from chunkshop.sinks.sqlite import SqliteSink, _HNSW_WARNED


@pytest.fixture
def dsn(monkeypatch):
    monkeypatch.setenv("SQLITE_TEST_PATH", ":memory:")
    return "SQLITE_TEST_PATH"


def _cfg(dsn_env, mode="overwrite", **kw) -> TargetConfig:
    return TargetConfig(
        type="sqlite", dsn_env=dsn_env, database="ignored",
        table="chunks", mode=mode, **kw,
    )


def _make_chunks(doc_id, n=3):
    return [
        Chunk(
            doc_id=doc_id, seq_num=i,
            original_content=f"chunk {i}",
            embedded_content=f"chunk {i}",
            metadata={"lang": "en", "section": f"s{i}"},
        ) for i in range(n)
    ]


def test_sc003_basic_ingest(dsn):
    """SC-003: SqliteSink writes to chunks + chunks_vec atomically."""
    cfg = _cfg(dsn, source_tag="t1")
    sink = SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("d1", 3)
    embs = np.random.rand(3, 4).astype(np.float32)
    sink.write_document("d1", chunks, embs, [["a"], ["b"], ["c"]])
    assert sink.count_docs() == 1


def test_sc008_append_dim_mismatch(dsn):
    """SC-008: append-mode preflight on SQLite errors clearly on dim mismatch."""
    cfg1 = _cfg(dsn, source_tag="t1")
    sink1 = SqliteSink(cfg1, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    # NOTE: :memory: DB resets per-connection; this test is meaningful only with file-based DBs.
    # For :memory: we use a single backend instance.
    cfg2 = _cfg(dsn, mode="append", source_tag="t2")
    sink2 = SqliteSink(cfg2, sink1.backend, embed_dim=8)
    with pytest.raises(RuntimeError, match=r"target dim 4 != cell embed_dim 8"):
        sink2.create_table()


def test_sc009_overwrite_foreign_tag(dsn):
    """SC-009: overwrite refuses to drop a table holding a foreign source_tag."""
    cfg1 = _cfg(dsn, source_tag="t1")
    sink1 = SqliteSink(cfg1, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _cfg(dsn, source_tag="t2")
    sink2 = SqliteSink(cfg2, sink1.backend, embed_dim=4)
    with pytest.raises(RuntimeError, match=r"foreign source_tag"):
        sink2.create_table()


def test_sc010_delete_orphans_both_tables(dsn, tmp_path, monkeypatch):
    """SC-010: delete_orphans deletes from chunks AND chunks_vec."""
    # Use a file-backed DB so the second write sees the first one
    db_file = tmp_path / "x.db"
    monkeypatch.setenv("SQLITE_FILE_PATH", str(db_file))
    cfg = _cfg("SQLITE_FILE_PATH", source_tag="t1", delete_orphans=True)
    sink = SqliteSink(cfg, SQLiteBackend(dsn_env="SQLITE_FILE_PATH"), embed_dim=4)
    sink.create_table()
    sink.write_document("d1", _make_chunks("d1", 5), np.random.rand(5, 4).astype(np.float32), [[]] * 5)
    sink.write_document("d1", _make_chunks("d1", 2), np.random.rand(2, 4).astype(np.float32), [[]] * 2)

    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {sink._fq_main()}")
        assert cur.fetchone()[0] == 2
        cur.execute(f"SELECT COUNT(*) FROM {sink._fq_vec()}")
        assert cur.fetchone()[0] == 2


def test_sc011_sc016_hnsw_logs_warning_once(dsn, caplog):
    """SC-011 / SC-016: hnsw=true on SQLite logs a warning once per process."""
    _HNSW_WARNED.clear()
    with caplog.at_level(logging.WARNING):
        cfg = _cfg(dsn, source_tag="t1", hnsw=True)
        SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
        # Second instance — no second warning
        SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    warnings = [r for r in caplog.records if "no-op" in r.message]
    assert len(warnings) == 1


def test_sc012_promote_metadata(dsn):
    """SC-012: promote_metadata uses json_extract to surface a JSON-path value."""
    cfg = _cfg(dsn, source_tag="t1",
               promote_metadata=[PromoteColumn(path="lang", type="text")])
    sink = SqliteSink(cfg, SQLiteBackend(dsn_env=dsn), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("d1", 1)
    sink.write_document("d1", chunks, np.random.rand(1, 4).astype(np.float32), [[]])
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        # promote_metadata column was ALTER-added; verify it exists and matches
        cur.execute(f"SELECT lang FROM {sink._fq_main()}")
        rows = cur.fetchall()
        # Note: promote columns are ADD COLUMN with no value population from json automatically;
        # the Sink fills them via _jsonb_path_get during write. So they should match.
        assert rows[0][0] == "en"
```

- [ ] **Step 2: Write SQLite source integration test**

```python
# python/tests/chunkshop/test_source_sqlite.py
"""SC-004: SqliteTableSource reads source rows."""
import pytest

pytest.importorskip("sqlite_vec")

from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.config import SqliteTableSource
from chunkshop.sources.sqlite_table import SqliteTableSource as Source


def test_sc004_iter_documents(tmp_path, monkeypatch):
    db = tmp_path / "src.db"
    monkeypatch.setenv("SQLITE_SRC_PATH", str(db))
    be = SQLiteBackend(dsn_env="SQLITE_SRC_PATH")
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute('CREATE TABLE "docs" (id TEXT PRIMARY KEY, body TEXT, lang TEXT)')
        cur.executemany('INSERT INTO "docs" VALUES (?, ?, ?)', [
            ("a", "first body", "en"),
            ("b", "second body", "fr"),
        ])
        conn.commit()

    cfg = SqliteTableSource(
        type="sqlite_table", dsn_env="SQLITE_SRC_PATH", database="ignored",
        table="docs", id_column="id", content_column="body",
        metadata_columns=["lang"],
    )
    docs = list(Source(cfg).iter_documents())
    assert len(docs) == 2
    by_id = {d.id: d for d in docs}
    assert by_id["a"].content == "first body"
    assert by_id["a"].metadata == {"lang": "en"}
    assert by_id["b"].metadata == {"lang": "fr"}
```

- [ ] **Step 3: Add `sample-sqlite.yaml` (gates SC-015)**

```yaml
# docs/samples/sample-sqlite.yaml
# SQLite end-to-end example. No external infrastructure required.
#
# From the chunkshop repo root:
#   export SQLITE_PATH=/tmp/chunkshop-sample.db
#   chunkshop ingest --config docs/samples/sample-sqlite.yaml
cell_name: samples_sqlite_demo

source:
  type: files
  glob: docs/samples/*-*.md
  id_from: stem
  encoding: utf-8

chunker:
  type: hierarchy
  prefix_heading: true
  min_section_chars: 100

embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  threads: 4
  batch_size: 64

extractor:
  type: none

target:
  type: sqlite
  dsn_env: SQLITE_PATH
  database: ignored          # SQLite has no schema concept; required by config but unused
  table: handbook
  mode: overwrite
  hnsw: false                # logged warning if true; sqlite-vec is brute-force KNN

runtime:
  omp_num_threads: 4
  heartbeat_every: 5
  log_path: /tmp/chunkshop-sample-sqlite.log
```

- [ ] **Step 4: Run all SQLite tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-v4/python
uv run pytest tests/chunkshop/test_sink_sqlite.py tests/chunkshop/test_source_sqlite.py -v
```

Expected: all PASS.

Validate the sample YAML:

```bash
uv run python -c "from chunkshop.config import load_config; load_config('../docs/samples/sample-sqlite.yaml'); print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Commit + DC-3 checkpoint**

```bash
git add python/tests/chunkshop/test_sink_sqlite.py \
        python/tests/chunkshop/test_source_sqlite.py \
        docs/samples/sample-sqlite.yaml
git commit -m "test(sqlite): integration tests + sample YAML — gates SC-003,004,008..013,015,016

DC-3 checkpoint: SQLite backend complete (sink + source + sample). The two-table
chunks/chunks_vec layout works; HNSW degrades gracefully; all per-backend modes
(overwrite/append/create_if_missing) verified."
```

---

## Phase 6 — `backends/mariadb.py`

### Task 21: Add `[mariadb]` extra; create `backends/mariadb.py` skeleton

**Files:**
- Modify: `python/pyproject.toml`
- Create: `python/src/chunkshop/backends/mariadb.py`
- Test: `python/tests/chunkshop/test_backend_mariadb.py`

- [ ] **Step 1: Add the optional dep**

In `python/pyproject.toml`, under `[project.optional-dependencies]`, add:

```toml
mariadb = ["PyMySQL>=1.1"]
```

If an `all-backends` extra exists, update it; otherwise add:

```toml
all-backends = ["chunkshop[mariadb]"]
```

Install:

```bash
cd /home/yonk/yonk-tools/chunkshop-v4/python
uv sync --extra dev --extra extractors --extra mariadb
```

- [ ] **Step 2: Write failing tests**

```python
# python/tests/chunkshop/test_backend_mariadb.py
import pytest

# Skip the whole module if PyMySQL isn't installed
pytest.importorskip("pymysql")

from chunkshop.backends.mariadb import MariaDBBackend


@pytest.fixture
def be():
    return MariaDBBackend(dsn_env="DUMMY_DSN")


def test_name_and_supports_upsert(be):
    assert be.name == "mariadb"
    assert be.supports_upsert is True


def test_quote_ident_uses_backticks(be):
    assert be.quote_ident("my_table") == "`my_table`"


def test_quote_ident_escapes_embedded_backtick(be):
    assert be.quote_ident("weird`name") == "`weird``name`"


def test_fq_table(be):
    assert be.fq_table("chunkshop", "chunks") == "`chunkshop`.`chunks`"
```

- [ ] **Step 3: Run tests to verify failure**

```bash
uv run pytest tests/chunkshop/test_backend_mariadb.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement skeleton**

```python
# python/src/chunkshop/backends/mariadb.py
"""MariaDB backend (≥11.7 — VECTOR type required). PyMySQL-based connection."""
from __future__ import annotations
import os
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import pymysql


class MariaDBBackend:
    """Backend Protocol implementation for MariaDB 11.7+ (native VECTOR type)."""

    name: Literal["mariadb"] = "mariadb"
    supports_upsert: bool = True

    def __init__(self, dsn_env: str):
        self._dsn_env = dsn_env

    @contextmanager
    def connect(self) -> Iterator[Any]:
        # PyMySQL doesn't accept a DSN string directly; parse from env.
        # Expected env value: mysql://user:pass@host:port/dbname
        dsn = os.environ[self._dsn_env]
        kwargs = _parse_mysql_dsn(dsn)
        conn = pymysql.connect(**kwargs)
        try:
            yield conn
        finally:
            conn.close()

    def quote_ident(self, name: str) -> str:
        return "`" + name.replace("`", "``") + "`"

    def fq_table(self, db: str, table: str) -> str:
        return f"{self.quote_ident(db)}.{self.quote_ident(table)}"


def _parse_mysql_dsn(dsn: str) -> dict:
    """Parse mysql://user:pass@host:port/dbname into PyMySQL kwargs.

    The connect-time `dbname` is NOT the chunkshop "database" (which is the
    target schema/db); PyMySQL needs an initial DB to connect, often the
    same one chunkshop will write to. The CHUNKSHOP_TEST_DSN_MARIADB env var
    documents this expectation.
    """
    from urllib.parse import urlparse, unquote
    parsed = urlparse(dsn)
    if parsed.scheme not in ("mysql", "mariadb"):
        raise ValueError(f"expected mysql:// or mariadb:// DSN, got {parsed.scheme!r}")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": parsed.username and unquote(parsed.username),
        "password": parsed.password and unquote(parsed.password),
        "database": parsed.path.lstrip("/") or None,
        "charset": "utf8mb4",
        "autocommit": False,
    }
```

- [ ] **Step 5: Run tests + commit**

```bash
uv run pytest tests/chunkshop/test_backend_mariadb.py -v
```

Expected: 4 PASS.

```bash
git add python/pyproject.toml \
        python/src/chunkshop/backends/mariadb.py \
        python/tests/chunkshop/test_backend_mariadb.py
git commit -m "feat(backends/mariadb): connect ctx-mgr + identifier safety + [mariadb] extra"
```

---

### Task 22: `backends/mariadb.py` — type DDL + literals + json_path_sql

**Files:**
- Modify: `python/src/chunkshop/backends/mariadb.py`
- Modify: `python/tests/chunkshop/test_backend_mariadb.py`

- [ ] **Step 1: Append failing tests**

```python
# Append to python/tests/chunkshop/test_backend_mariadb.py
import json
import numpy as np


def test_vector_type_ddl(be):
    assert be.vector_type_ddl(384) == "VECTOR(384)"


def test_json_type_ddl(be):
    assert be.json_type_ddl() == "JSON"


def test_tags_array_type_ddl(be):
    # MariaDB has no native array — use JSON
    assert be.tags_array_type_ddl() == "JSON"


def test_text_pk_type_ddl(be):
    # MariaDB primary keys need bounded length on TEXT — use VARCHAR
    assert be.text_pk_type_ddl() == "VARCHAR(255)"


def test_timestamp_now_default_ddl(be):
    out = be.timestamp_now_default_ddl()
    assert "TIMESTAMP" in out.upper()
    assert "CURRENT_TIMESTAMP" in out.upper() or "NOW()" in out.upper()


def test_vector_literal_uses_vec_fromtext(be):
    arr = np.array([0.1, 0.2], dtype=np.float32)
    out = be.vector_literal(arr)
    # Returns a SQL expression string; parameter binding wraps with VEC_FromText
    assert "VEC_FromText" in out
    assert "0.1" in out and "0.2" in out


def test_tags_literal_serializes_to_json(be):
    out = be.tags_literal(["a", "b"])
    # MariaDB stores as JSON column; pass JSON string for parameter binding
    assert json.loads(out) == ["a", "b"]


def test_json_literal_serializes(be):
    out = be.json_literal({"a": 1})
    assert json.loads(out) == {"a": 1}


def test_json_path_sql_simple(be):
    # JSON_UNQUOTE+JSON_EXTRACT — leaf path
    assert be.json_path_sql("metadata", "lang") == "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.lang'))"


def test_json_path_sql_nested(be):
    out = be.json_path_sql("metadata", "entities.ORG")
    assert out == "JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.entities.ORG'))"
```

- [ ] **Step 2: Run + verify failures**

```bash
uv run pytest tests/chunkshop/test_backend_mariadb.py -v
```

Expected: 10 FAIL with `AttributeError`.

- [ ] **Step 3: Append implementation**

Add inside `MariaDBBackend` class:

```python
import json
import numpy as np
from typing import Any


    # Type DDL fragments
    def vector_type_ddl(self, dim: int) -> str:
        return f"VECTOR({dim})"

    def json_type_ddl(self) -> str:
        return "JSON"

    def tags_array_type_ddl(self) -> str:
        # MariaDB has no native array type; store tags as a JSON array.
        return "JSON"

    def text_pk_type_ddl(self) -> str:
        # InnoDB primary keys can't be unbounded TEXT; use VARCHAR(255).
        return "VARCHAR(255)"

    def timestamp_now_default_ddl(self) -> str:
        return "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"

    # Value literals
    def vector_literal(self, arr: np.ndarray) -> str:
        # Returned as a SQL expression; the writer must use it as a value
        # expression rather than %s parameter (because VEC_FromText is a function).
        text = "[" + ",".join(f"{x:.6f}" for x in arr) + "]"
        return f"VEC_FromText('{text}')"

    def tags_literal(self, tags: list[str]) -> str:
        return json.dumps(list(tags))

    def json_literal(self, obj: Any) -> str:
        return json.dumps(obj)

    # JSON path extraction
    def json_path_sql(self, col_expr: str, dotted_path: str) -> str:
        # Identifier validation upstream guarantees safe segments. JSON_UNQUOTE
        # strips surrounding quotes from the JSON_EXTRACT result for text values.
        return f"JSON_UNQUOTE(JSON_EXTRACT({col_expr},'$.{dotted_path}'))"
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/chunkshop/test_backend_mariadb.py -v
```

Expected: all 14 PASS.

```bash
git add python/src/chunkshop/backends/mariadb.py \
        python/tests/chunkshop/test_backend_mariadb.py
git commit -m "feat(backends/mariadb): type DDL fragments + literals + json_path_sql"
```

---

### Task 23: `backends/mariadb.py` — DDL primitives + upsert_clause + emit_chunks_table_ddl + introspection + GET_LOCK + register

**Files:**
- Modify: `python/src/chunkshop/backends/mariadb.py`
- Modify: `python/src/chunkshop/backends/__init__.py`
- Modify: `python/tests/chunkshop/test_backend_mariadb.py`

- [ ] **Step 1: Append failing tests**

```python
# Append to python/tests/chunkshop/test_backend_mariadb.py
from chunkshop.backends.base import ColSpec


def test_create_database_sql(be):
    out = be.create_database_sql("chunkshop_test")
    assert "CREATE DATABASE IF NOT EXISTS" in out
    assert "`chunkshop_test`" in out


def test_add_column_if_not_exists_sql(be):
    out = be.add_column_if_not_exists_sql("`db`.`tbl`", "newcol", "JSON")
    assert "ALTER TABLE" in out
    # MariaDB doesn't support ADD COLUMN IF NOT EXISTS until 10.0.2; chunkshop
    # targets 11.7+, so the syntax is supported.
    assert "ADD COLUMN IF NOT EXISTS" in out
    assert "`newcol` JSON" in out


def test_drop_table_sql(be):
    assert be.drop_table_sql("`db`.`tbl`") == "DROP TABLE `db`.`tbl`"


def test_upsert_clause(be):
    out = be.upsert_clause(["id"], ["content", "metadata"])
    assert "ON DUPLICATE KEY UPDATE" in out
    # MariaDB form: col = VALUES(col)  — note id is implied by the PK collision
    assert "`content` = VALUES(`content`)" in out
    assert "`metadata` = VALUES(`metadata`)" in out


def test_emit_chunks_table_ddl_inline_vector_index(be):
    cols = [
        ColSpec("id", "VARCHAR(255)", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "VARCHAR(255)", nullable=False),
        ColSpec("seq_num", "INT", nullable=False),
        ColSpec("embedding", "VECTOR(384)", nullable=False),
    ]
    out = be.emit_chunks_table_ddl(fq="`db`.`chunks`", cols=cols, hnsw=True, dim=384)
    # MariaDB embeds the VECTOR INDEX inline in CREATE TABLE
    assert len(out) >= 1
    create = out[0]
    assert create.startswith("CREATE TABLE IF NOT EXISTS")
    assert "VECTOR INDEX" in create or "VECTOR KEY" in create
    assert "`embedding`" in create


def test_emit_chunks_table_ddl_engine_clause(be):
    out = be.emit_chunks_table_ddl(
        fq="`db`.`chunks`",
        cols=[ColSpec("id", "VARCHAR(255)", is_primary_key=True, nullable=False)],
        hnsw=False,
        dim=384,
        engine="InnoDB",
    )
    assert "ENGINE=InnoDB" in out[0]
```

- [ ] **Step 2: Append implementation**

```python
# Append inside MariaDBBackend class
from contextlib import contextmanager


    def upsert_clause(self, key_cols: list[str], update_cols: list[str]) -> str:
        del key_cols  # MariaDB doesn't name keys explicitly; PK collision triggers it
        if not update_cols:
            # MariaDB lacks "DO NOTHING"; the workaround is INSERT IGNORE — which
            # is set on the INSERT statement itself, not the conflict clause.
            # Sink will detect empty update_cols and switch to INSERT IGNORE.
            return ""
        sets = ", ".join(
            f"{self.quote_ident(c)} = VALUES({self.quote_ident(c)})" for c in update_cols
        )
        return f"ON DUPLICATE KEY UPDATE {sets}"

    def create_database_sql(self, name: str) -> str:
        return f"CREATE DATABASE IF NOT EXISTS {self.quote_ident(name)}"

    def add_column_if_not_exists_sql(self, fq: str, col: str, type_ddl: str) -> str:
        return f"ALTER TABLE {fq} ADD COLUMN IF NOT EXISTS {self.quote_ident(col)} {type_ddl}"

    def drop_table_sql(self, fq: str) -> str:
        return f"DROP TABLE {fq}"

    def emit_chunks_table_ddl(
        self, fq: str, cols: list, hnsw: bool, dim: int, engine: str | None = None,
    ) -> list[str]:
        del dim  # encoded in the embedding column's type_ddl
        col_lines = []
        pk_cols = []
        for c in cols:
            line = f"  {self.quote_ident(c.name)} {c.type_ddl}"
            if c.default is not None:
                line += f" DEFAULT {c.default}"
            if not c.nullable:
                line += " NOT NULL"
            col_lines.append(line)
            if c.is_primary_key:
                pk_cols.append(c.name)
        lines = ",\n".join(col_lines)
        if pk_cols:
            pk = ", ".join(self.quote_ident(c) for c in pk_cols)
            lines += f",\n  PRIMARY KEY ({pk})"
        # Inline vector index on the embedding column — MariaDB syntax
        if hnsw:
            lines += ",\n  VECTOR INDEX `vec_idx` (`embedding`)"
        engine_clause = f" ENGINE={engine or 'InnoDB'}"
        # Index for doc_id, seq_num — created inline as well
        lines += ",\n  KEY `doc_seq_idx` (`doc_id`, `seq_num`)"
        return [f"CREATE TABLE IF NOT EXISTS {fq} (\n{lines}\n){engine_clause}"]

    # Introspection
    def table_exists(self, cur: Any, db: str, table: str) -> bool:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s",
            (db, table),
        )
        return cur.fetchone()[0] > 0

    def embedding_dim(self, cur: Any, db: str, table: str) -> int | None:
        # MariaDB exposes vector column dim via information_schema.columns.column_type.
        # Format is "vector(N)" lowercase.
        cur.execute(
            "SELECT column_type FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND column_name='embedding'",
            (db, table),
        )
        r = cur.fetchone()
        if not r:
            return None
        import re as _re
        m = _re.match(r"^vector\((\d+)\)$", r[0].lower())
        return int(m.group(1)) if m else None

    # Concurrent-create lock — MariaDB GET_LOCK/RELEASE_LOCK
    @contextmanager
    def with_create_lock(self, cur: Any, key: str) -> Iterator[None]:
        # GET_LOCK accepts a max name length of 64 chars
        lock_name = f"chunkshop_{key}"[:64]
        cur.execute("SELECT GET_LOCK(%s, 30)", (lock_name,))
        got = cur.fetchone()[0]
        if got != 1:
            raise RuntimeError(f"could not acquire MariaDB lock {lock_name!r} within 30s")
        try:
            yield
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
```

- [ ] **Step 3: Register in `load_backend`**

Update `python/src/chunkshop/backends/__init__.py`:

```python
"""Backend layer: connection lifecycle + dialect helpers per database backend."""
from chunkshop.backends.base import Backend, ColSpec
from chunkshop.backends.postgres import PostgresBackend


def load_backend(name: str, dsn_env: str) -> Backend:
    if name == "postgres":
        return PostgresBackend(dsn_env=dsn_env)
    if name == "mariadb":
        # Lazy import — PyMySQL is an optional dep
        from chunkshop.backends.mariadb import MariaDBBackend
        return MariaDBBackend(dsn_env=dsn_env)
    raise ValueError(f"unknown backend: {name!r}")


__all__ = ["Backend", "ColSpec", "PostgresBackend", "load_backend"]
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/chunkshop/test_backend_mariadb.py tests/chunkshop/test_backends_load.py -v
```

Expected: all PASS.

Add a load_backend test:

```python
# Append to python/tests/chunkshop/test_backends_load.py
import pytest
pytest.importorskip("pymysql")


def test_load_backend_mariadb():
    from chunkshop.backends import load_backend
    be = load_backend(name="mariadb", dsn_env="DUMMY")
    assert be.name == "mariadb"
```

```bash
uv run pytest tests/chunkshop/test_backends_load.py -v
```

```bash
git add python/src/chunkshop/backends/mariadb.py \
        python/src/chunkshop/backends/__init__.py \
        python/tests/chunkshop/test_backend_mariadb.py \
        python/tests/chunkshop/test_backends_load.py
git commit -m "feat(backends/mariadb): full Backend Protocol surface + load_backend dispatch"
```

---

## Phase 7 — `sinks/mariadb.py`

### Task 24: Add MariaDB to `TargetConfig.type` literal

**Files:**
- Modify: `python/src/chunkshop/config.py`
- Test: `python/tests/chunkshop/test_config_target_v4.py`

- [ ] **Step 1: Write failing test**

```python
# Append to python/tests/chunkshop/test_config_target_v4.py
def test_target_type_mariadb_accepted():
    cfg = TargetConfig(
        type="mariadb",
        dsn_env="MARIADB_DSN",
        database="chunkshop",
        table="my_chunks",
        mode="overwrite",
    )
    assert cfg.type == "mariadb"
```

- [ ] **Step 2: Run + fail**

```bash
uv run pytest tests/chunkshop/test_config_target_v4.py::test_target_type_mariadb_accepted -v
```

Expected: ValidationError — `"mariadb"` not yet in the literal (current: `Literal["postgres", "sqlite"]` after Task 18).

- [ ] **Step 3: Modify `TargetConfig.type` literal**

```python
# in python/src/chunkshop/config.py, change:
# was: type: Literal["postgres", "sqlite"]   (set in Task 18)
type: Literal["postgres", "sqlite", "mariadb"]   # ClickHouse added in a later release
```

- [ ] **Step 4: Run + pass + commit**

```bash
uv run pytest tests/chunkshop/test_config_target_v4.py -v
```

```bash
git add python/src/chunkshop/config.py \
        python/tests/chunkshop/test_config_target_v4.py
git commit -m "feat(config): TargetConfig.type accepts mariadb"
```

---

### Task 25: Create `sinks/mariadb.py` — port the chunkshop data-model semantics for MariaDB

**Files:**
- Create: `python/src/chunkshop/sinks/mariadb.py`
- Test: `python/tests/chunkshop/test_sink_mariadb.py` (integration; skip if no DSN)

This task mirrors `sinks/pg.py` but adapts to MariaDB's syntax differences:
- Vector literal goes inline as `VEC_FromText('[…]')`, not as a `%s::vector` parameter.
- `tags` and `metadata` are JSON columns; pass JSON-string parameters.
- Upsert clause is `ON DUPLICATE KEY UPDATE`.
- `delete_orphans` works the same (MariaDB supports DELETE in a transaction).

- [ ] **Step 1: Create the file**

```python
# python/src/chunkshop/sinks/mariadb.py
"""MariaDB sink — chunks-table writer using the MariaDBBackend dialect."""
from __future__ import annotations
import os
from typing import Any

import numpy as np

from chunkshop.backends.base import ColSpec
from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig


def _jsonb_path_get(meta: dict, path: str):
    """Same dict navigation as sinks/pg._jsonb_path_get."""
    cur = meta
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _canonical_cols(dim: int) -> list[ColSpec]:
    return [
        ColSpec("id", "VARCHAR(255)", nullable=False, is_primary_key=True),
        ColSpec("doc_id", "VARCHAR(255)", nullable=False),
        ColSpec("seq_num", "INT", nullable=False),
        ColSpec("original_content", "LONGTEXT", nullable=False),
        ColSpec("embedded_content", "LONGTEXT", nullable=False),
        ColSpec("tags", "JSON", nullable=False, default="(JSON_ARRAY())"),
        ColSpec("metadata", "JSON", nullable=False, default="(JSON_OBJECT())"),
        ColSpec("embedding", f"VECTOR({dim})", nullable=False),
        ColSpec("source", "VARCHAR(255)"),
        ColSpec("created_at", "TIMESTAMP", nullable=False, default="CURRENT_TIMESTAMP"),
    ]


class MariaDbSink:
    """Per-document writer to a MariaDB chunks table.

    Mirrors PgSink's contract on MariaDB. delete_orphans uses DELETE-in-transaction
    same as PG. Vector literal is composed inline (VEC_FromText) rather than
    parameter-bound, since MariaDB doesn't have a vector parameter type adapter.
    """

    def __init__(self, cfg: TargetConfig, backend: MariaDBBackend, embed_dim: int):
        self.cfg = cfg
        self.backend = backend
        self.embed_dim = embed_dim

    def _fq(self) -> str:
        return self.backend.fq_table(self.cfg.database_name, self.cfg.table)

    # -- create_table dispatch ----------------------------------------------
    def create_table(self) -> None:
        with self.backend.connect() as conn:
            cur = conn.cursor()
            with self.backend.with_create_lock(cur, self.cfg.database_name):
                cur.execute(self.backend.create_database_sql(self.cfg.database_name))
                if self.cfg.mode == "overwrite":
                    self._overwrite_create(cur)
                elif self.cfg.mode == "append":
                    self._append_preflight(cur)
                elif self.cfg.mode == "create_if_missing":
                    self._create_if_missing(cur)
                else:
                    raise ValueError(f"unknown mode: {self.cfg.mode}")
            conn.commit()

    def _create_base_ddl(self, cur) -> None:
        for stmt in self.backend.emit_chunks_table_ddl(
            fq=self._fq(),
            cols=_canonical_cols(self.embed_dim),
            hnsw=self.cfg.hnsw,
            dim=self.embed_dim,
        ):
            cur.execute(stmt)
        self._ensure_promote_columns(cur)

    def _ensure_promote_columns(self, cur) -> None:
        for pc in self.cfg.promote_metadata:
            mariadb_type = _pg_type_to_mariadb(pc.type)
            cur.execute(self.backend.add_column_if_not_exists_sql(
                self._fq(), pc.column_name, mariadb_type
            ))

    def _overwrite_create(self, cur) -> None:
        if self._table_exists(cur) and not self.cfg.force_overwrite:
            cur.execute(f"SELECT DISTINCT source FROM {self._fq()} WHERE source IS NOT NULL LIMIT 10")
            existing_tags = {r[0] for r in cur.fetchall()}
            my_tag = self.cfg.source_tag
            foreign = existing_tags - ({my_tag} if my_tag else set())
            if foreign:
                raise RuntimeError(
                    f"overwrite refuses to drop {self.cfg.database_name}.{self.cfg.table}: "
                    f"foreign source_tag values {sorted(foreign)!r}"
                )
        if self._table_exists(cur):
            cur.execute(self.backend.drop_table_sql(self._fq()))
        self._create_base_ddl(cur)

    def _create_if_missing(self, cur) -> None:
        if not self._table_exists(cur):
            self._create_base_ddl(cur)
        else:
            cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "VARCHAR(255)"))
            self._ensure_promote_columns(cur)

    def _append_preflight(self, cur) -> None:
        if not self._table_exists(cur):
            raise RuntimeError(f"append mode: table {self._fq()} does not exist")
        current_dim = self.backend.embedding_dim(cur, self.cfg.database_name, self.cfg.table)
        if current_dim is None:
            raise RuntimeError(f"append mode: {self._fq()} has no embedding column")
        if current_dim != self.embed_dim:
            raise RuntimeError(
                f"append mode: target dim {current_dim} != cell embed_dim {self.embed_dim}"
            )
        cur.execute(self.backend.add_column_if_not_exists_sql(self._fq(), "source", "VARCHAR(255)"))
        self._ensure_promote_columns(cur)

    def _table_exists(self, cur) -> bool:
        return self.backend.table_exists(cur, self.cfg.database_name, self.cfg.table)

    # -- write_document -----------------------------------------------------
    def write_document(
        self, doc_id: str, chunks: list[Chunk], embeddings: np.ndarray,
        tags_per_chunk: list[list[str]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks / embeddings length mismatch")
        if len(chunks) != len(tags_per_chunk):
            raise ValueError(f"chunks / tags length mismatch")

        promote = self.cfg.promote_metadata
        base_col_names = [
            "id", "doc_id", "seq_num", "original_content", "embedded_content",
            "tags", "metadata", "embedding", "source",
        ]
        all_col_names = base_col_names + [pc.column_name for pc in promote]
        # update_cols: skip id/doc_id/seq_num AND source (write-once on conflict)
        update_cols = base_col_names[3:8] + [pc.column_name for pc in promote]
        upsert_sql = self.backend.upsert_clause([], update_cols)

        cols_sql = ", ".join(self.backend.quote_ident(c) for c in all_col_names)
        # Vector literal goes INLINE as VEC_FromText(...); other values via %s
        # PyMySQL placeholder is %s as well.
        non_vec_count = len(all_col_names) - 1  # everything except embedding
        # Build placeholders in the same column order; embedding slot = literal
        placeholders = []
        for c in all_col_names:
            if c == "embedding":
                placeholders.append("__VEC_PLACEHOLDER__")  # replaced per-row
            else:
                placeholders.append("%s")
        vals_sql_template = ", ".join(placeholders)

        rows = []
        sql_per_row = []  # one SQL string per row (because vector literal is inline)
        params_per_row = []
        for c, emb, tags in zip(chunks, embeddings, tags_per_chunk):
            vec_expr = self.backend.vector_literal(emb)  # "VEC_FromText('[…]')"
            row_vals_sql = vals_sql_template.replace("__VEC_PLACEHOLDER__", vec_expr)
            row_sql = (
                f"INSERT INTO {self._fq()} ({cols_sql}) VALUES ({row_vals_sql}) {upsert_sql}"
            )
            base_params = [
                f"{c.doc_id}::{c.seq_num}",
                c.doc_id,
                c.seq_num,
                c.original_content,
                c.embedded_content,
                self.backend.tags_literal(tags),
                self.backend.json_literal(c.metadata),
                # embedding handled inline above
                self.cfg.source_tag,
            ]
            promote_params = [_jsonb_path_get(c.metadata, pc.path) for pc in promote]
            sql_per_row.append(row_sql)
            params_per_row.append(tuple(base_params + promote_params))

        with self.backend.connect() as conn:
            cur = conn.cursor()
            for sql_, params in zip(sql_per_row, params_per_row):
                cur.execute(sql_, params)
            if self.cfg.delete_orphans:
                cur.execute(
                    f"DELETE FROM {self._fq()} WHERE doc_id = %s AND seq_num >= %s",
                    (doc_id, len(chunks)),
                )
            conn.commit()

    def count_docs(self) -> int:
        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(DISTINCT doc_id) FROM {self._fq()}")
            return cur.fetchone()[0]


_PG_TO_MARIADB_TYPE = {
    "text": "TEXT",
    "text[]": "JSON",      # MariaDB has no native array
    "int": "INT",
    "bigint": "BIGINT",
    "boolean": "BOOLEAN",
    "jsonb": "JSON",
    "timestamptz": "TIMESTAMP",
    "date": "DATE",
}


def _pg_type_to_mariadb(pg_type: str) -> str:
    """Translate a PromoteColumn.type (PG-flavored) to MariaDB column type DDL."""
    return _PG_TO_MARIADB_TYPE.get(pg_type, pg_type)
```

- [ ] **Step 2: Add to `load_sink`**

```python
# python/src/chunkshop/sinks/__init__.py — modify load_sink:
def load_sink(cfg, embed_dim: int):
    if cfg.type == "postgres":
        backend = load_backend(name="postgres", dsn_env=cfg.dsn_env)
        return PgSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    if cfg.type == "mariadb":
        from chunkshop.sinks.mariadb import MariaDbSink
        backend = load_backend(name="mariadb", dsn_env=cfg.dsn_env)
        return MariaDbSink(cfg=cfg, backend=backend, embed_dim=embed_dim)
    raise ValueError(f"unknown target type: {cfg.type!r}")
```

- [ ] **Step 3: Verify import**

```bash
uv run python -c "from chunkshop.sinks.mariadb import MariaDbSink; from chunkshop.sinks import load_sink; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit (integration tests come in next task)**

```bash
git add python/src/chunkshop/sinks/mariadb.py \
        python/src/chunkshop/sinks/__init__.py
git commit -m "feat(sinks/mariadb): MariaDbSink port — full chunkshop data-model semantics"
```

---

### Task 26: MariaDB sink integration tests — gate SC-005, SC-008, SC-009, SC-010, SC-011, SC-012

**Files:**
- Create: `python/tests/chunkshop/test_sink_mariadb.py`

This task is gated on a reachable MariaDB. Document the env var: `CHUNKSHOP_TEST_DSN_MARIADB=mysql://user:pass@host:3306/dbname`. Skip if unset.

- [ ] **Step 1: Write integration tests**

```python
# python/tests/chunkshop/test_sink_mariadb.py
"""Integration tests for MariaDbSink. Skipped unless $CHUNKSHOP_TEST_DSN_MARIADB is set
and points to a reachable MariaDB 11.7+ instance."""
import os
import numpy as np
import pytest

pytest.importorskip("pymysql")

from chunkshop.config import TargetConfig, PromoteColumn
from chunkshop.chunkers.base import Chunk
from chunkshop.sinks.mariadb import MariaDbSink
from chunkshop.backends.mariadb import MariaDBBackend


DSN_VAR = "CHUNKSHOP_TEST_DSN_MARIADB"
DSN = os.environ.get(DSN_VAR)
pytestmark = pytest.mark.skipif(not DSN, reason=f"{DSN_VAR} not set")


@pytest.fixture
def db_name():
    return "chunkshop_test_v4"


@pytest.fixture
def cleanup(db_name):
    """Drop the test database before and after each test."""
    os.environ.setdefault(DSN_VAR, DSN or "")
    be = MariaDBBackend(dsn_env=DSN_VAR)
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        conn.commit()
    yield
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        conn.commit()


def _make_cfg(db_name, table="chunks", mode="overwrite", **kw) -> TargetConfig:
    return TargetConfig(
        type="mariadb",
        dsn_env=DSN_VAR,
        database=db_name,
        table=table,
        mode=mode,
        **kw,
    )


def _make_chunks(doc_id, n=3):
    return [
        Chunk(
            doc_id=doc_id, seq_num=i,
            original_content=f"chunk {i} body",
            embedded_content=f"chunk {i} body",
            metadata={"lang": "en", "section": f"sec_{i}"},
        )
        for i in range(n)
    ]


def test_sc003_create_and_write(cleanup, db_name):
    """SC-005: a MariaDB sink can ingest a sample doc into a chunks table."""
    cfg = _make_cfg(db_name, source_tag="t1")
    sink = MariaDbSink(cfg, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("doc1", n=3)
    embs = np.random.rand(3, 4).astype(np.float32)
    tags = [["a"], ["b"], ["c"]]
    sink.write_document("doc1", chunks, embs, tags)
    assert sink.count_docs() == 1


def test_sc006_append_dim_mismatch_clear_error(cleanup, db_name):
    """SC-008: append-mode preflight fails clearly on dim mismatch."""
    cfg1 = _make_cfg(db_name, mode="overwrite", source_tag="t1")
    sink1 = MariaDbSink(cfg1, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _make_cfg(db_name, mode="append", source_tag="t2")
    sink2 = MariaDbSink(cfg2, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=8)
    with pytest.raises(RuntimeError, match=r"target dim 4 != cell embed_dim 8"):
        sink2.create_table()


def test_sc007_overwrite_foreign_tag_safety(cleanup, db_name):
    """SC-009: overwrite mode refuses to drop a table holding a foreign source_tag."""
    cfg1 = _make_cfg(db_name, mode="overwrite", source_tag="t1")
    sink1 = MariaDbSink(cfg1, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink1.create_table()
    sink1.write_document("d1", _make_chunks("d1", 1), np.random.rand(1, 4).astype(np.float32), [[]])

    cfg2 = _make_cfg(db_name, mode="overwrite", source_tag="t2")
    sink2 = MariaDbSink(cfg2, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    with pytest.raises(RuntimeError, match=r"foreign source_tag"):
        sink2.create_table()


def test_sc008_delete_orphans(cleanup, db_name):
    """SC-010: delete_orphans removes chunks with seq_num beyond the new chunkset."""
    cfg = _make_cfg(db_name, source_tag="t1", delete_orphans=True)
    sink = MariaDbSink(cfg, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    # Initial write: 5 chunks
    sink.write_document("d1", _make_chunks("d1", 5), np.random.rand(5, 4).astype(np.float32), [[]] * 5)
    # Re-write with 2 chunks → seq 2, 3, 4 should be deleted
    sink.write_document("d1", _make_chunks("d1", 2), np.random.rand(2, 4).astype(np.float32), [[]] * 2)
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {sink._fq()}")
        assert cur.fetchone()[0] == 2


def test_sc009_hnsw_index_present(cleanup, db_name):
    """SC-011: HNSW vector index gets created on the chunks table."""
    cfg = _make_cfg(db_name, source_tag="t1", hnsw=True)
    sink = MariaDbSink(cfg, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SHOW INDEX FROM {sink._fq()}")
        rows = cur.fetchall()
        # Look for our vec_idx — index_type column is typically index 10 in SHOW INDEX
        names = [r[2] for r in rows]
        assert "vec_idx" in names


def test_sc010_promote_metadata_jsonpath(cleanup, db_name):
    """SC-012: promote_metadata extracts a JSON-path value into a typed column."""
    cfg = _make_cfg(
        db_name, source_tag="t1",
        promote_metadata=[PromoteColumn(path="lang", type="text")],
    )
    sink = MariaDbSink(cfg, MariaDBBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    chunks = _make_chunks("d1", 1)  # metadata={"lang": "en", ...}
    sink.write_document("d1", chunks, np.random.rand(1, 4).astype(np.float32), [[]])
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT lang FROM {sink._fq()}")
        rows = cur.fetchall()
        assert rows and rows[0][0] == "en"
```

- [ ] **Step 2: Run integration tests**

```bash
# Set $CHUNKSHOP_TEST_DSN_MARIADB to a reachable MariaDB 11.7+ instance
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3306/mysql"
uv run pytest tests/chunkshop/test_sink_mariadb.py -v
```

Expected: 6 PASS (all SC-005, SC-008..SC-012 green). If any fail, fix in `sinks/mariadb.py` and `backends/mariadb.py`.

- [ ] **Step 3: Commit (MariaDB sink milestone)**

```bash
git add python/tests/chunkshop/test_sink_mariadb.py
git commit -m "test(sinks/mariadb): integration tests gating SC-005,008,009,010,011,012

MariaDB sink milestone: full feature parity with PG sink for the chunkshop-
canonical data model (modes, foreign-tag safety, append preflight,
delete_orphans, HNSW vector index, promote_metadata). DC-4 fires after
the MariaDB *source* lands (Phase 8, Task 28)."
```

---

## Phase 8 — Source-side modularity (PG + MariaDB)

### Task 27: Refactor `sources/pg_table.py` to use `backends/postgres.py`

**Files:**
- Modify: `python/src/chunkshop/sources/pg_table.py`

The current pg_table source uses psycopg + sql.SQL directly. Refactoring it to go through `PostgresBackend.connect()` and `quote_ident()` doesn't change behavior; it removes duplication and prepares for the MariaDB twin.

- [ ] **Step 1: Modify `pg_table.py`**

```python
# python/src/chunkshop/sources/pg_table.py
from __future__ import annotations
import datetime as _dt
import os
from decimal import Decimal
from typing import Any, Iterator

import psycopg
from psycopg import sql

from chunkshop.backends.postgres import PostgresBackend
from chunkshop.config import PgTableSource as Cfg
from chunkshop.sources.base import Document


def _json_safe(v: Any) -> Any:
    """Coerce psycopg-returned values to JSON-serializable forms.

    Decimal → float, datetime/date/time → ISO 8601, bytes → b64.
    """
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        import base64
        return base64.b64encode(v).decode("ascii")
    return v


class PgTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = PostgresBackend(dsn_env=cfg.dsn_env)

    def iter_documents(self) -> Iterator[Document]:
        cols = [self.cfg.id_column, self.cfg.content_column]
        title_idx = None
        if self.cfg.title_column:
            title_idx = len(cols)
            cols.append(self.cfg.title_column)
        meta_start = len(cols)
        cols.extend(self.cfg.metadata_columns)
        ident_cols = [sql.Identifier(c) for c in cols]
        query = sql.SQL("SELECT {cols} FROM {schema}.{table}").format(
            cols=sql.SQL(", ").join(ident_cols),
            schema=sql.Identifier(self.cfg.database_name),
            table=sql.Identifier(self.cfg.table),
        )
        if self.cfg.where:
            query = query + sql.SQL(" WHERE ") + sql.SQL(self.cfg.where)
        with self.backend.connect() as conn, conn.cursor() as cur:
            cur.execute(query)
            for row in cur:
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

The query composition still uses psycopg's `sql.SQL` because it's the safest way to compose parameter-able SQL with the psycopg driver. The backend abstraction here only owns the connection lifecycle.

- [ ] **Step 2: Run existing pg_table tests**

```bash
uv run pytest tests/chunkshop/test_metadata_promotion_e2e.py tests/chunkshop/test_multi_source_ingest.py -v
```

Expected: PASS (skipped if no PG DSN).

- [ ] **Step 3: Commit**

```bash
git add python/src/chunkshop/sources/pg_table.py
git commit -m "refactor(sources/pg_table): use PostgresBackend for connection lifecycle"
```

---

### Task 28: Add `MariaDbTableSource` to config + create the source

**Files:**
- Modify: `python/src/chunkshop/config.py`
- Create: `python/src/chunkshop/sources/mariadb_table.py`
- Modify: `python/src/chunkshop/sources/__init__.py`
- Test: `python/tests/chunkshop/test_source_mariadb.py`

- [ ] **Step 1: Add `MariaDbTableSource` to config.py**

In `config.py` after the `PgTableSource` class, add:

```python
class MariaDbTableSource(_Base):
    type: Literal["mariadb_table"]
    dsn_env: str
    database_name: str = Field(alias="database")
    table: str
    id_column: str
    content_column: str
    title_column: Optional[str] = None
    where: Optional[str] = None
    metadata_columns: list[str] = Field(default_factory=list)
```

Update the `SourceConfig` union to include the new MariaDB type (`SqliteTableSource` was added in Task 19; preserve it here):

```python
SourceConfig = Annotated[
    Union[FilesSource, JsonCorpusSource, PgTableSource, SqliteTableSource,
          MariaDbTableSource, HttpSource, S3Source, InlineSource],
    Field(discriminator="type"),
]
```

- [ ] **Step 2: Create the source module**

```python
# python/src/chunkshop/sources/mariadb_table.py
from __future__ import annotations
import datetime as _dt
import json
from decimal import Decimal
from typing import Any, Iterator

from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.config import MariaDbTableSource as Cfg
from chunkshop.sources.base import Document


def _json_safe(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        import base64
        return base64.b64encode(v).decode("ascii")
    if isinstance(v, str):
        # MariaDB's JSON column round-trips as Python str. If we know a column
        # came from a JSON column we'd parse it, but that's information we
        # don't track in metadata_columns. Caller can parse downstream.
        return v
    return v


class MariaDbTableSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.backend = MariaDBBackend(dsn_env=cfg.dsn_env)

    def iter_documents(self) -> Iterator[Document]:
        cols = [self.cfg.id_column, self.cfg.content_column]
        title_idx = None
        if self.cfg.title_column:
            title_idx = len(cols)
            cols.append(self.cfg.title_column)
        meta_start = len(cols)
        cols.extend(self.cfg.metadata_columns)

        # Build the SELECT with backticks via the backend's quote_ident
        cols_sql = ", ".join(self.backend.quote_ident(c) for c in cols)
        fq = self.backend.fq_table(self.cfg.database_name, self.cfg.table)
        query = f"SELECT {cols_sql} FROM {fq}"
        params: tuple = ()
        if self.cfg.where:
            # `where` is documented as trusted operator input — same contract as PgTableSource.
            query += f" WHERE {self.cfg.where}"

        with self.backend.connect() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            for row in cur:
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

- [ ] **Step 3: Register in `sources/__init__.py`**

Read the existing `sources/__init__.py` to find the `load_source` function, and add a branch:

```python
# In load_source(cfg):
if isinstance(cfg, MariaDbTableSource):
    from chunkshop.sources.mariadb_table import MariaDbTableSource as Impl
    return Impl(cfg)
```

(The exact form depends on existing dispatch style; match what's already there.)

- [ ] **Step 4: Write integration test (SC-006)**

```python
# python/tests/chunkshop/test_source_mariadb.py
import os
import pytest

pytest.importorskip("pymysql")

from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.config import MariaDbTableSource
from chunkshop.sources.mariadb_table import MariaDbTableSource as Source

DSN_VAR = "CHUNKSHOP_TEST_DSN_MARIADB"
DSN = os.environ.get(DSN_VAR)
pytestmark = pytest.mark.skipif(not DSN, reason=f"{DSN_VAR} not set")


def test_sc004_iter_documents(monkeypatch):
    """SC-006: a MariaDB source can read source rows into the pipeline."""
    db_name = "chunkshop_src_test"
    be = MariaDBBackend(dsn_env=DSN_VAR)
    # Setup test table
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
        cur.execute(f"DROP TABLE IF EXISTS `{db_name}`.`docs`")
        cur.execute(f"""
            CREATE TABLE `{db_name}`.`docs` (
                id VARCHAR(64) PRIMARY KEY,
                body TEXT NOT NULL,
                lang VARCHAR(8)
            )
        """)
        cur.execute(
            f"INSERT INTO `{db_name}`.`docs` VALUES (%s, %s, %s), (%s, %s, %s)",
            ("a", "first body", "en", "b", "second body", "fr"),
        )
        conn.commit()

    cfg = MariaDbTableSource(
        type="mariadb_table",
        dsn_env=DSN_VAR,
        database=db_name,
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

    # Cleanup
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE `{db_name}`")
        conn.commit()
```

- [ ] **Step 5: Run + commit (gates SC-006)**

```bash
uv run pytest tests/chunkshop/test_source_mariadb.py -v
```

Expected: 1 PASS (skipped if no DSN).

```bash
git add python/src/chunkshop/config.py \
        python/src/chunkshop/sources/mariadb_table.py \
        python/src/chunkshop/sources/__init__.py \
        python/tests/chunkshop/test_source_mariadb.py
git commit -m "feat(sources/mariadb_table): MariaDB source with metadata_columns

DC-4 checkpoint: SC-005 + SC-006 green; MariaDB backend complete (sink + source)."
```

---

## Phase 9 — Cross-backend + final

### Task 29: Cross-backend smoke test (read MariaDB → write PG) — gate SC-007

**Files:**
- Create: `python/tests/chunkshop/test_cross_backend.py`

- [ ] **Step 1: Write the test**

```python
# python/tests/chunkshop/test_cross_backend.py
"""Cross-backend pipeline: MariaDB source → PG sink.

Skipped unless BOTH $CHUNKSHOP_TEST_DSN (PG) and $CHUNKSHOP_TEST_DSN_MARIADB are set.
"""
import os
import numpy as np
import pytest

pytest.importorskip("pymysql")

from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.backends.postgres import PostgresBackend
from chunkshop.config import (
    MariaDbTableSource, TargetConfig, FastembedEmbedder, NoneExtractor,
    SentenceAwareChunker, IdentityFramerConfig, RuntimeConfig, CellConfig,
)
from chunkshop.runner import run_cell


PG_DSN_VAR = "CHUNKSHOP_TEST_DSN"
MARIADB_DSN_VAR = "CHUNKSHOP_TEST_DSN_MARIADB"
PG_DSN = os.environ.get(PG_DSN_VAR)
MARIADB_DSN = os.environ.get(MARIADB_DSN_VAR)
pytestmark = pytest.mark.skipif(
    not (PG_DSN and MARIADB_DSN),
    reason=f"both {PG_DSN_VAR} and {MARIADB_DSN_VAR} required",
)


def test_sc005_read_mariadb_write_pg(monkeypatch):
    """SC-007: a cell that reads MariaDB and writes PG completes end-to-end."""
    src_db = "chunkshop_xb_src"
    sink_db = "chunkshop_xb_sink"

    # Seed source data in MariaDB
    be_md = MariaDBBackend(dsn_env=MARIADB_DSN_VAR)
    with be_md.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{src_db}`")
        cur.execute(f"CREATE DATABASE `{src_db}`")
        cur.execute(f"""
            CREATE TABLE `{src_db}`.`docs` (
                id VARCHAR(64) PRIMARY KEY,
                body TEXT NOT NULL
            )
        """)
        cur.execute(
            f"INSERT INTO `{src_db}`.`docs` VALUES (%s, %s)",
            ("doc1", "Hello world. This is a test sentence. " * 10),
        )
        conn.commit()

    cfg = CellConfig(
        cell_name="xb_test",
        source=MariaDbTableSource(
            type="mariadb_table", dsn_env=MARIADB_DSN_VAR, database=src_db,
            table="docs", id_column="id", content_column="body",
        ),
        framer=IdentityFramerConfig(),
        chunker=SentenceAwareChunker(max_chars=200, min_chars=50),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-base-en-v1.5-int8",
            dim=768, threads=2, batch_size=8,
        ),
        extractor=NoneExtractor(),
        target=TargetConfig(
            type="postgres", dsn_env=PG_DSN_VAR, database=sink_db,
            table="chunks", mode="overwrite", source_tag="xb_test", hnsw=False,
        ),
        runtime=RuntimeConfig(omp_num_threads=2),
    )

    result = run_cell(cfg)
    assert result.error is None
    assert result.docs_processed == 1
    assert result.chunks_written > 0

    # Verify PG side
    be_pg = PostgresBackend(dsn_env=PG_DSN_VAR)
    with be_pg.connect() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{sink_db}"."chunks"')
        assert cur.fetchone()[0] == result.chunks_written

    # Cleanup
    with be_md.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE `{src_db}`")
        conn.commit()
    with be_pg.connect() as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA "{sink_db}" CASCADE')
        conn.commit()
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/chunkshop/test_cross_backend.py -v
```

Expected: 1 PASS (or skip if either DSN unset).

```bash
git add python/tests/chunkshop/test_cross_backend.py
git commit -m "test(cross-backend): MariaDB source → PG sink end-to-end

DC-5 checkpoint: SC-007 green. Cross-backend pipelines work as designed."
```

---

### Task 30: `docker-compose.test.yaml` + final SC verification

**Files:**
- Create: `docker-compose.test.yaml` (at repo root)
- Modify: `CLAUDE.md` (add MariaDB DSN env var note)

- [ ] **Step 1: Create docker-compose.test.yaml**

```yaml
# /home/yonk/yonk-tools/chunkshop-v4/docker-compose.test.yaml
# Spin up Postgres + MariaDB containers for chunkshop integration tests.
#
# Usage:
#   docker compose -f docker-compose.test.yaml up -d
#   export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
#   export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
#   uv run pytest -q
#   docker compose -f docker-compose.test.yaml down -v
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: chunkshop_test
    ports:
      - "5434:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 3s
      retries: 10

  mariadb:
    image: mariadb:11.7
    environment:
      MARIADB_ROOT_PASSWORD: rootpw
      MARIADB_DATABASE: chunkshop_test
    ports:
      - "3307:3306"
    healthcheck:
      test: ["CMD-SHELL", "healthcheck.sh --connect --innodb_initialized"]
      interval: 5s
      timeout: 3s
      retries: 20
```

- [ ] **Step 2: Update CLAUDE.md with the MariaDB DSN convention**

Append to the "Postgres for integration tests" section (or rename to "Databases for integration tests"):

```markdown
### Databases for integration tests

Tests that talk to a database connect via env vars and **skip** if unreachable:

- `$CHUNKSHOP_TEST_DSN` — Postgres (default `postgresql://postgres:postgres@localhost:5434/chunkshop_test` if `docker-compose.test.yaml` is up)
- `$CHUNKSHOP_TEST_DSN_MARIADB` — MariaDB 11.7+ (default `mysql://root:rootpw@localhost:3307/chunkshop_test` if `docker-compose.test.yaml` is up)

Spin both up:

    docker compose -f docker-compose.test.yaml up -d

Cross-backend tests (`test_cross_backend.py`) require both DSNs set.
```

- [ ] **Step 3: Run the full test suite under both DSNs**

```bash
docker compose -f /home/yonk/yonk-tools/chunkshop-v4/docker-compose.test.yaml up -d
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"
export CHUNKSHOP_TEST_DSN_MARIADB="mysql://root:rootpw@localhost:3307/chunkshop_test"
cd /home/yonk/yonk-tools/chunkshop-v4/python
uv run pytest -q 2>&1 | tail -20
```

Expected: full suite green. Record passing count vs. Task 0 baseline (must be ≥ baseline + new MariaDB tests + cross-backend test).

- [ ] **Step 4: Verify all SC-001..SC-017**

Hand-walk the spec's §12 success criteria:

- SC-001 (bakeoff parity): `uv run pytest tests/chunkshop/test_bakeoff_e2e.py` — green
- SC-002 (existing tests pass): full suite green
- SC-003 (SQLite sink): `test_sink_sqlite.py` — green
- SC-004 (SQLite source): `test_source_sqlite.py` — green
- SC-005 (MariaDB sink): `test_sink_mariadb.py` — green
- SC-006 (MariaDB source): `test_source_mariadb.py` — green
- SC-007 (cross-backend smoke tests): `test_cross_backend.py` — green
- SC-008..SC-012 (per-backend mode/safety/orphans/HNSW/promote): asserted across `test_sink_sqlite.py` and `test_sink_mariadb.py`
- SC-013 (SQL-injection regression): identifier-validation tests cover all three backends
- SC-014 (sample YAMLs): `test_end_to_end_samples_corpus.py` — green
- SC-015 (sample-sqlite.yaml): demonstrates SQLite end-to-end ingest
- SC-016 (SQLite HNSW warning): captured via `caplog` in `test_sink_sqlite.py`
- SC-017 (Pipeline.delete_document on SQLite): two-table delete verified in pipeline test

If any SC is unverified, add a focused task before considering the plan complete.

- [ ] **Step 5: Final commit + DC-FINAL**

```bash
git add docker-compose.test.yaml CLAUDE.md
git commit -m "infra: docker-compose.test.yaml + CLAUDE.md MariaDB DSN convention

DC-FINAL: all SC-001..SC-017 verified green with PG + MariaDB reachable
(SQLite tests need no infrastructure). v4.0 first-ship target (PG-refactor
+ SQLite + MariaDB) is feature-complete on the experimental/v4-modular-backends
branch.""
```

- [ ] **Step 6: Write end-of-work summary** (per the user's `summary-pattern` rule)

Write a `CHANGES MADE / DIDN'T TOUCH / POTENTIAL CONCERNS` summary to console for the user, covering all 24 tasks.

---

## Out of Scope (confirmed deferred)

- ClickHouse implementation (`backends/clickhouse.py`, `sinks/clickhouse.py`, `sources/clickhouse_table.py`).
- ClickHouse JSON-output research spike for `metadata_columns`.
- Cross-backend bakeoff suite (factorial bakeoff stays PG-only).
- Rich HNSW tuning per backend (only `target.hnsw: bool` in v4.0).
- Connection pooling — per-document short-lived connections preserved.
- Async I/O.
- Migration scripts from 0.3.x → v4.0.
- Vector distance function selection (cosine hardcoded).
- Backend hot-swap mid-pipeline.
- Rust/Go ports.

## Open Questions Resolved by This Plan

| Spec OQ | Resolution in plan |
|---|---|
| OQ1 — MariaDB driver pilot | `PyMySQL>=1.1` (Task 21). Document `mysqlclient` as a perf upgrade in CLAUDE.md if benchmark shows regression. |
| OQ2 — MariaDB minimum version | 11.7+; tests assume `VECTOR` type and `INDEX … VECTOR INDEX` syntax (Task 23, Task 25). |
| OQ3 — CH minimum version | Out of scope this plan. |
| OQ4 — CH `metadata_columns` JSON | Out of scope this plan. |
| OQ5 — Test infrastructure | `docker-compose.test.yaml` shipped (Task 30). Env-var-only fallback also documented. |
| OQ6 — HNSW operator class on PG | Stays hardcoded `vector_cosine_ops` (Task 5). |
| OQ7 — `created_at` semantics on CH | Out of scope this plan. |
| OQ8 — `psycopg` optional? | Stays required dep. MariaDB is the optional one (Task 21). |
