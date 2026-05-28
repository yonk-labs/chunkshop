# CS-2 — Typed `edge_kind` on `code_edges` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Active mission brief:** `skill-output/mission-brief/Mission-Brief-cs2-typed-edge-kind.md` — re-read at every ⛔ drift checkpoint.

**Goal:** Add a typed, codegraph-aligned `edge_kind` column to chunkshop's PG `code_edges` table; map the 3 existing uppercase `edge_type` values; surface `--edge-kind` on `chunkshop impact-of`. Strictly additive — `edge_type` column unchanged so the CLI and pg-raggraph keep working byte-identical.

**Architecture:** Add a 12-value `EdgeKind` Literal + `EDGE_KINDS` tuple + `_EDGE_TYPE_TO_KIND` mapping to `chunkshop.extractors.code_relationships`. Patch the extractor's `_emit()` chokepoint so every edge derives `edge_kind` from `edge_type` via the mapping (single point of update — only one place to forget). Patch `write_edges_schema` to add the new column + 12-value CHECK constraint + supporting index. Patch `write_edges` INSERT to emit `edge_kind` (and preserve it on `ON CONFLICT DO UPDATE`). Patch `impact-of` CLI to accept a `--edge-kind` option that ANDs into the recursive-CTE filter alongside `--edge-type`.

**Tech Stack:** Python 3.11+, `psycopg` v3, `click`, pytest. PG via `docker-compose.test.yaml`. No new runtime deps.

**Out of scope reminder (from brief):** sink files (mariadb/sqlite/clickhouse), Rust, CS-5 provenance columns, pg-raggraph patches, `edge_type` rename/deprecation, emitting the 9 currently-unused codegraph values.

---

## Task 1: Add `EDGE_KINDS` + `EdgeKind` + mapping helper (pure Python, no PG)

**Brief criteria:** SC-002, SC-005

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py` (add module-level constants + helper near top of file, just below imports around line 54)
- Create: `python/tests/chunkshop/test_edge_kind_types.py`

- [ ] **Step 1: Write the failing test**

```python
# python/tests/chunkshop/test_edge_kind_types.py
"""Pure-Python sanity tests for the CS-2 EdgeKind ontology.

No PG, no fixtures — just the constants and mapping helper imported
straight from chunkshop.extractors.code_relationships.
"""
from __future__ import annotations

import pytest


def test_edge_kinds_tuple_is_codegraph_canonical_set() -> None:
    """EDGE_KINDS must be the exact 12-value codegraph ontology, in canonical order."""
    from chunkshop.extractors.code_relationships import EDGE_KINDS

    assert EDGE_KINDS == (
        "contains", "calls", "imports", "exports",
        "extends", "implements", "references",
        "type_of", "returns", "instantiates",
        "overrides", "decorates",
    )
    assert len(EDGE_KINDS) == 12
    # All lowercase, snake_case, no duplicates.
    assert all(k.islower() and k.replace("_", "").isalpha() for k in EDGE_KINDS)
    assert len(set(EDGE_KINDS)) == 12


def test_edge_kind_literal_is_importable() -> None:
    """EdgeKind is a Literal[...] that mypy/pyright can narrow."""
    from chunkshop.extractors.code_relationships import EdgeKind  # noqa: F401

    # Literal types have no runtime API beyond __args__.
    from typing import get_args
    assert set(get_args(EdgeKind)) == {
        "contains", "calls", "imports", "exports",
        "extends", "implements", "references",
        "type_of", "returns", "instantiates",
        "overrides", "decorates",
    }


@pytest.mark.parametrize(
    ("legacy", "kind"),
    [
        ("CALLS", "calls"),
        ("INHERITS", "extends"),
        ("IMPLEMENTS", "implements"),
    ],
)
def test_edge_type_to_kind_mapping(legacy: str, kind: str) -> None:
    """The 3 existing uppercase edge_type values map to their codegraph equivalents."""
    from chunkshop.extractors.code_relationships import edge_type_to_kind

    assert edge_type_to_kind(legacy) == kind


def test_edge_type_to_kind_rejects_unknown_value() -> None:
    """Unknown edge_type → explicit error, not silent default."""
    from chunkshop.extractors.code_relationships import edge_type_to_kind

    with pytest.raises(ValueError, match="unknown edge_type"):
        edge_type_to_kind("BOGUS")
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_edge_kind_types.py -v`
Expected: 4 failures — `ImportError: cannot import name 'EDGE_KINDS'` etc.

- [ ] **Step 3: Implement minimal types + mapping**

Add immediately after the import block in `python/src/chunkshop/extractors/code_relationships.py` (after line 54, before the regex constants):

```python
# ---------------------------------------------------------------------------
# CS-2: typed EdgeKind ontology
# ---------------------------------------------------------------------------
#
# The 12-value vocabulary is ported verbatim from codegraph's `EdgeKind`
# TypeScript union (see ../skill-output/codegraph-patterns/CODEGRAPH-SOURCE.md
# §1). This is additive — the legacy uppercase `edge_type` column stays
# untouched; `edge_kind` is the new typed source-of-truth column.

from typing import Literal

EdgeKind = Literal[
    "contains", "calls", "imports", "exports",
    "extends", "implements", "references",
    "type_of", "returns", "instantiates",
    "overrides", "decorates",
]

EDGE_KINDS: tuple[EdgeKind, ...] = (
    "contains", "calls", "imports", "exports",
    "extends", "implements", "references",
    "type_of", "returns", "instantiates",
    "overrides", "decorates",
)

# Mapping from the 3 legacy uppercase edge_type values this extractor
# emits today to their codegraph EdgeKind equivalents. CS-1 will populate
# the other 9 kinds when it ports the 20-language extractor stack; until
# then they're valid against the CHECK constraint but no code path writes
# them.
_EDGE_TYPE_TO_KIND: dict[str, EdgeKind] = {
    "CALLS": "calls",
    "INHERITS": "extends",
    "IMPLEMENTS": "implements",
}


def edge_type_to_kind(edge_type: str) -> EdgeKind:
    """Translate a legacy uppercase ``edge_type`` value into its EdgeKind.

    Raises ``ValueError`` on unknown values so a typo in a new emission
    site fails loudly instead of silently writing NULL.
    """
    try:
        return _EDGE_TYPE_TO_KIND[edge_type]
    except KeyError:
        raise ValueError(
            f"unknown edge_type {edge_type!r} — must be one of "
            f"{sorted(_EDGE_TYPE_TO_KIND)}"
        ) from None
```

Also add `EdgeKind`, `EDGE_KINDS`, `edge_type_to_kind` to the module's `__all__` if one exists (none does today; skip).

- [ ] **Step 4: Run test, verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_edge_kind_types.py -v`
Expected: 6 passed (3 from parametrize on mapping + 3 other tests).

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/extractors/code_relationships.py python/tests/chunkshop/test_edge_kind_types.py
git commit -m "$(cat <<'EOF'
feat(code_relationships): add EdgeKind / EDGE_KINDS / edge_type_to_kind (CS-2 prep)

Pure-Python types and mapping helper, no PG schema or extractor write-path
changes yet. Lays the source-of-truth EdgeKind Literal used by the
extractor (next commit) and the CLI (commit after).
EOF
)"
```

---

## Task 2: Patch `write_edges_schema` DDL — add `edge_kind` column + CHECK + index

**Brief criteria:** SC-001 (constraint), SC-003 (legacy column preserved)

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py:485-521` (the `write_edges_schema` body)
- Create: `python/tests/chunkshop/test_code_edges_typed.py` (PG-backed, will be extended in Task 4)

- [ ] **Step 1: Write the failing test**

```python
# python/tests/chunkshop/test_code_edges_typed.py
"""Live-PG tests for the CS-2 typed edge_kind column.

Skips cleanly when CHUNKSHOP_TEST_DSN isn't reachable so the suite stays
green for contributors without docker-compose.test.yaml running. Each
test creates + drops its own schema so the tests don't leak state.
"""
from __future__ import annotations

import os
import uuid

import pytest

DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/chunkshop_test",
)


def _pg_reachable(dsn: str) -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(DSN),
    reason="CHUNKSHOP_TEST_DSN not reachable; bring up docker-compose.test.yaml",
)


@pytest.fixture
def schema(request):
    """One throwaway schema per test, dropped at teardown."""
    import psycopg
    from psycopg import sql

    name = f"chunkshop_cs2_{uuid.uuid4().hex[:8]}"
    yield name
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def test_schema_includes_edge_kind_column_with_check_constraint(schema: str) -> None:
    """write_edges_schema creates code_edges with edge_kind + 12-value CHECK."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        # Column exists, correct type, NOT NULL, has a DEFAULT.
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'code_edges' "
            "ORDER BY column_name",
            (schema,),
        )
        cols = {row[0]: row for row in cur.fetchall()}
        assert "edge_kind" in cols
        assert cols["edge_kind"][1] == "text"
        assert cols["edge_kind"][2] == "NO"
        assert cols["edge_kind"][3] is not None  # has DEFAULT

        # Legacy edge_type column UNTOUCHED — SC-003 regression guard.
        assert "edge_type" in cols
        assert cols["edge_type"][1] == "text"
        assert cols["edge_type"][2] == "NO"


def test_edge_kind_check_constraint_rejects_invalid_value(schema: str) -> None:
    """CHECK constraint refuses non-codegraph values."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql, errors as pg_errors

        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        with pytest.raises(pg_errors.CheckViolation):
            cur.execute(
                sql.SQL(
                    "INSERT INTO {fq} "
                    "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                    " dst_node_id, confidence, evidence, edge_kind) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"
                ).format(fq=fq),
                ("p", "CALLS", "a", "b", "id_a", "id_b", 0.9, "{}", "bogus_kind"),
            )


def test_edge_kind_check_accepts_all_12_codegraph_values(schema: str) -> None:
    """Every value in EDGE_KINDS satisfies the CHECK constraint."""
    import psycopg

    from chunkshop.extractors.code_relationships import EDGE_KINDS, write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql

        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        for i, kind in enumerate(EDGE_KINDS):
            cur.execute(
                sql.SQL(
                    "INSERT INTO {fq} "
                    "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                    " dst_node_id, confidence, evidence, edge_kind) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"
                ).format(fq=fq),
                ("p", "CALLS", f"a{i}", f"b{i}", f"id_a{i}", f"id_b{i}", 0.9, "{}", kind),
            )
        conn.commit()
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {fq}").format(fq=fq)
        )
        assert cur.fetchone()[0] == 12
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd python && docker compose -f docker-compose.test.yaml up -d && uv run --no-sync pytest tests/chunkshop/test_code_edges_typed.py -v`
Expected: 3 failures — column doesn't exist / CHECK doesn't exist.

(If PG isn't reachable the tests will skip; bring up the compose stack first.)

- [ ] **Step 3: Patch the DDL**

In `python/src/chunkshop/extractors/code_relationships.py`, replace the `CREATE TABLE` block inside `write_edges_schema` (currently lines 489-502) with:

```python
        cur.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {fq} ("
                " project_id text NOT NULL,"
                " edge_type text NOT NULL,"
                " src_fqn text NOT NULL,"
                " dst_fqn text NOT NULL,"
                " src_node_id text NOT NULL,"
                " dst_node_id text NOT NULL,"
                " confidence double precision NOT NULL,"
                " evidence jsonb,"
                # CS-2: typed codegraph EdgeKind ontology (12 values). Default
                # is 'references' so a pre-CS-2 row inserted by an older client
                # still satisfies NOT NULL; the extractor's write_edges path
                # always supplies an explicit value via edge_type_to_kind.
                " edge_kind text NOT NULL DEFAULT 'references'"
                "   CHECK (edge_kind IN ('contains','calls','imports','exports',"
                "                        'extends','implements','references',"
                "                        'type_of','returns','instantiates',"
                "                        'overrides','decorates')),"
                " PRIMARY KEY (project_id, edge_type, src_node_id, dst_node_id))"
            ).format(fq=fq)
        )
```

Add a fourth index immediately after the `code_edges_confident_idx` block (after current line 519):

```python
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {ix} ON {fq} "
                "(project_id, edge_kind)"
            ).format(ix=sql.Identifier("code_edges_kind_idx"), fq=fq)
        )
```

- [ ] **Step 4: Run test, verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_code_edges_typed.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/extractors/code_relationships.py python/tests/chunkshop/test_code_edges_typed.py
git commit -m "$(cat <<'EOF'
feat(code_relationships): add edge_kind column + 12-value CHECK to code_edges (CS-2)

Schema-only patch: write_edges_schema now creates code_edges with the
codegraph EdgeKind ontology as a CHECK constraint. Legacy edge_type
column is untouched (still in PK, still uppercase) — additive change,
no breaking impact on existing readers (pg-raggraph, chunkshop impact-of).

DEFAULT 'references' lets older clients keep inserting; the next commit
wires the extractor's write path to populate edge_kind explicitly from
the legacy edge_type via edge_type_to_kind.
EOF
)"
```

- [ ] **Step 6: ⛔ Drift Checkpoint DC-001**

Re-read `skill-output/mission-brief/Mission-Brief-cs2-typed-edge-kind.md`. Verify:
- SC-001: CHECK constraint lists all 12 codegraph values verbatim? ✓ check DDL string.
- SC-003: `edge_type` column literally unchanged in the DDL string (name, type, PK membership)? ✓ diff against original.
- Constraint "Additivity": no rename/transformation/removal of `edge_type`? ✓
- Out of scope: no sink files touched? `git diff --stat` should show only `code_relationships.py` + test files.

If any check fails, stop and fix before proceeding to Task 3.

---

## Task 3: Patch `_emit()` to derive `edge_kind` from `edge_type`

**Brief criteria:** SC-001, SC-002, SC-006

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py:252-288` (the `_emit` nested function inside `finalize`)
- Modify: `python/tests/chunkshop/test_code_relationships_extractor.py` (additive assertions; do NOT change existing `edge_type` assertions)

- [ ] **Step 1: Write the failing test (additive extension)**

Add to `python/tests/chunkshop/test_code_relationships_extractor.py` at the end of the file (after the last existing test):

```python
# ---------------------------------------------------------------------------
# CS-2: edge_kind alongside edge_type
# ---------------------------------------------------------------------------


def test_finalize_emits_edge_kind_alongside_edge_type() -> None:
    """Every edge from finalize() carries edge_kind mapped from edge_type."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        edge_type_to_kind,
    )

    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    # Minimal cross-file Python: a.py defines foo; b.py calls foo.
    ext.extract(
        "def foo():\n    pass\n",
        language="python",
        file_path="a.py",
    )
    ext.extract(
        "def bar():\n    foo()\n",
        language="python",
        file_path="b.py",
    )
    edges = ext.finalize(project_id="test")

    assert len(edges) >= 1
    for e in edges:
        # SC-003 regression: edge_type still present and uppercase.
        assert e["edge_type"] in ("CALLS", "INHERITS", "IMPLEMENTS")
        # SC-001/SC-002: edge_kind present and mapped.
        assert "edge_kind" in e
        assert e["edge_kind"] == edge_type_to_kind(e["edge_type"])


def test_finalize_emits_correct_edge_kind_for_inherits_and_implements() -> None:
    """INHERITS → extends, IMPLEMENTS → implements (Java path covers both)."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import CodeRelationshipsExtractor

    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    # Java: class Child extends Parent implements Iface
    ext.extract(
        "public class Parent {}\n",
        language="java",
        file_path="Parent.java",
    )
    ext.extract(
        "public interface Iface {}\n",
        language="java",
        file_path="Iface.java",
    )
    ext.extract(
        "public class Child extends Parent implements Iface {}\n",
        language="java",
        file_path="Child.java",
    )
    edges = ext.finalize(project_id="test")

    by_kind = {e["edge_kind"]: e for e in edges}
    assert "extends" in by_kind
    assert by_kind["extends"]["edge_type"] == "INHERITS"
    assert "implements" in by_kind
    assert by_kind["implements"]["edge_type"] == "IMPLEMENTS"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py::test_finalize_emits_edge_kind_alongside_edge_type tests/chunkshop/test_code_relationships_extractor.py::test_finalize_emits_correct_edge_kind_for_inherits_and_implements -v`
Expected: 2 failures — `KeyError: 'edge_kind'`.

- [ ] **Step 3: Patch `_emit` to derive edge_kind**

In `python/src/chunkshop/extractors/code_relationships.py`, replace the `edges.append(...)` block inside the `_emit` nested function (currently lines 278-288) with:

```python
            edges.append(
                {
                    "edge_type": edge_type,
                    # CS-2: typed codegraph EdgeKind, derived from edge_type
                    # via the canonical mapping. Single chokepoint — every
                    # emission path goes through _emit so this is the only
                    # site that needs to know about EdgeKind.
                    "edge_kind": edge_type_to_kind(edge_type),
                    "src_fqn": src_fqn,
                    "dst_fqn": dst_fqn,
                    "src_node_id": src_id,
                    "dst_node_id": dst_id,
                    "confidence": confidence,
                    "evidence": evidence,
                }
            )
```

(`edge_type_to_kind` is module-level; it's already in scope inside `finalize`.)

- [ ] **Step 4: Run test, verify it passes — and full extractor suite stays green**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py -v`
Expected: all tests pass (including the 2 new ones + every pre-existing test unchanged). If any pre-existing test fails, you mutated an existing assertion — back it out and patch additively.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/extractors/code_relationships.py python/tests/chunkshop/test_code_relationships_extractor.py
git commit -m "$(cat <<'EOF'
feat(code_relationships): emit edge_kind alongside edge_type in finalize (CS-2)

_emit() now derives edge_kind from edge_type via edge_type_to_kind so
every code path (CALLS unique/ambiguous, INHERITS, IMPLEMENTS) produces
a row with both columns. edge_type assertions in existing tests are
preserved verbatim — the change is additive.
EOF
)"
```

---

## Task 4: Patch `write_edges` INSERT to persist `edge_kind`

**Brief criteria:** SC-001, SC-006

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py:550-578` (the `write_edges` `rows` list comprehension + INSERT SQL)
- Modify: `python/tests/chunkshop/test_code_edges_typed.py` (add a round-trip test)

- [ ] **Step 1: Write the failing round-trip test**

Append to `python/tests/chunkshop/test_code_edges_typed.py`:

```python
def test_write_edges_round_trip_persists_edge_kind(schema: str) -> None:
    """write_edges persists edge_kind; SELECT round-trips identical values."""
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract("def foo():\n    pass\n", language="python", file_path="a.py")
    ext.extract("def bar():\n    foo()\n", language="python", file_path="b.py")

    n = write_edges(ext, dsn=DSN, schema=schema, project_id="rt")
    assert n >= 1

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql
        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        cur.execute(
            sql.SQL("SELECT edge_type, edge_kind FROM {fq} ORDER BY src_fqn, dst_fqn").format(fq=fq)
        )
        rows = cur.fetchall()
        assert len(rows) >= 1
        for edge_type, edge_kind in rows:
            assert edge_type == "CALLS"
            assert edge_kind == "calls"


def test_write_edges_on_conflict_preserves_edge_kind(schema: str) -> None:
    """Re-running write_edges updates edge_kind on conflict (not reverts to default)."""
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    def _run() -> int:
        ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
        ext.extract("def foo():\n    pass\n", language="python", file_path="a.py")
        ext.extract("def bar():\n    foo()\n", language="python", file_path="b.py")
        return write_edges(ext, dsn=DSN, schema=schema, project_id="rt")

    _run()
    _run()  # second run hits ON CONFLICT DO UPDATE

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql
        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        cur.execute(
            sql.SQL("SELECT edge_kind FROM {fq}").format(fq=fq)
        )
        kinds = {r[0] for r in cur.fetchall()}
        assert kinds == {"calls"}  # no row reverted to default 'references'
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_code_edges_typed.py::test_write_edges_round_trip_persists_edge_kind tests/chunkshop/test_code_edges_typed.py::test_write_edges_on_conflict_preserves_edge_kind -v`
Expected: 2 failures — `edge_kind` always equals `'references'` (the DEFAULT) because INSERT doesn't supply it.

- [ ] **Step 3: Patch `write_edges`**

In `python/src/chunkshop/extractors/code_relationships.py`, replace the `rows = [...]` comprehension and the `insert = sql.SQL(...)` block (currently lines 550-574) with:

```python
    rows = [
        (
            project_id,
            e["edge_type"],
            e["src_fqn"],
            e["dst_fqn"],
            e["src_node_id"],
            e["dst_node_id"],
            e["confidence"],
            Json(e.get("evidence") or {}),
            # CS-2: every finalize() edge now carries edge_kind (Task 3).
            e["edge_kind"],
        )
        for e in edges
    ]

    insert = sql.SQL(
        "INSERT INTO {fq} (project_id, edge_type, src_fqn, dst_fqn,"
        " src_node_id, dst_node_id, confidence, evidence, edge_kind) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (project_id, edge_type, src_node_id, dst_node_id) "
        "DO UPDATE SET"
        "  src_fqn = EXCLUDED.src_fqn,"
        "  dst_fqn = EXCLUDED.dst_fqn,"
        "  confidence = EXCLUDED.confidence,"
        "  evidence = EXCLUDED.evidence,"
        # CS-2: preserve edge_kind on update so a re-run with a changed
        # mapping (future ontology migration) doesn't leave stale values.
        "  edge_kind = EXCLUDED.edge_kind"
    ).format(fq=fq)
```

- [ ] **Step 4: Run test, verify it passes**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_code_edges_typed.py -v`
Expected: 5 passed (3 from Task 2 + 2 new).

Also re-run the runner-finalize wiring test to confirm nothing regressed:

```bash
cd python && uv run --no-sync pytest tests/chunkshop/test_runner_finalize_wires_edges.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/extractors/code_relationships.py python/tests/chunkshop/test_code_edges_typed.py
git commit -m "$(cat <<'EOF'
feat(code_relationships): write edge_kind in write_edges INSERT (CS-2)

INSERT now supplies edge_kind from the finalize() row; ON CONFLICT
DO UPDATE preserves it so re-runs after a mapping change don't leave
stale values. Round-trip + conflict regression tests added.
EOF
)"
```

- [ ] **Step 6: ⛔ Drift Checkpoint DC-002**

Re-read `skill-output/mission-brief/Mission-Brief-cs2-typed-edge-kind.md`. Verify:
- SC-001: every emitted edge has non-NULL `edge_kind`? ✓ (write_edges always supplies it)
- SC-002: mapping is exact for the 3 known values? ✓ (verified in Task 3 + Task 4 tests)
- SC-006: existing tests pass without modified assertions? Run `cd python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py tests/chunkshop/test_runner_finalize_wires_edges.py -v`; expected green. Inspect git diff on these test files — only additive lines (no removal/modification of `assert e["edge_type"] == ...` lines).
- Did I touch the 3 emission call sites directly (lines 296, 313, 326, 418/433, 447)? Should be **NO** — the patch is in `_emit()` only. If you edited call sites directly, undo and route through `_emit()`.

If any check fails, stop and fix before proceeding to Task 5.

---

## Task 5: `_impact_query_one_direction` accepts optional `edge_kind` filter

**Brief criteria:** SC-004

**Files:**
- Modify: `python/src/chunkshop/cli.py:837-938` (the `_impact_query_one_direction` helper)
- Modify: `python/tests/chunkshop/test_cli_impact_of.py` (add filter-composition tests)

- [ ] **Step 1: Inspect existing test file to match its fixture conventions**

Run: `cd python && head -50 tests/chunkshop/test_cli_impact_of.py` to understand its DSN-handling and schema-fixture pattern. Use the same approach for new tests (don't invent a new pattern).

- [ ] **Step 2: Write the failing tests**

Append to `python/tests/chunkshop/test_cli_impact_of.py` (match the file's existing fixture / skip style — patterns vary by file; the snippets below assume a `_pg_reachable` + schema fixture similar to Task 2's; adapt to match the file's actual style):

```python
# ---------------------------------------------------------------------------
# CS-2: --edge-kind filter
# ---------------------------------------------------------------------------


def test_impact_query_filters_by_edge_kind_when_supplied(...) -> None:
    """Passing edge_kind ANDs into the WHERE clause; mismatched kind returns nothing."""
    # Set up: write one CALLS edge (edge_kind='calls').
    # Query with edge_type='CALLS' + edge_kind='calls' → should return the row.
    # Query with edge_type='CALLS' + edge_kind='extends' → should return zero rows.
    # (Use the existing fixture pattern from this file for DSN + schema setup.)
    ...


def test_impact_query_unchanged_when_edge_kind_none(...) -> None:
    """edge_kind=None preserves byte-identical behavior with pre-CS-2 callers."""
    # Same setup; query with edge_type='CALLS' + edge_kind=None.
    # Expected: returns the row (no edge_kind filter applied).
    ...
```

(Stub bodies left for the engineer to fill using the file's existing fixtures — every other test in `test_cli_impact_of.py` already establishes DSN, schema, and writes seed rows; copy that pattern.)

- [ ] **Step 3: Run tests, verify they fail**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_cli_impact_of.py -v -k edge_kind`
Expected: failures — `_impact_query_one_direction` doesn't accept `edge_kind`.

- [ ] **Step 4: Patch the helper**

In `python/src/chunkshop/cli.py`, modify `_impact_query_one_direction`'s signature (around line 837) to accept an optional `edge_kind` keyword:

```python
def _impact_query_one_direction(
    dsn: str,
    *,
    schema: str,
    fqn: str,
    direction: str,
    depth: int,
    project_id: str,
    confidence_floor: float,
    edge_type: str,
    edge_kind: str | None = None,  # CS-2: optional typed-kind filter, ANDs in
) -> list[dict]:
```

Update both `WHERE` clauses inside the CTE (around lines 896 and 903) to conditionally include the edge_kind predicate. The cleanest approach: build the predicate fragment + params in Python, splice via `sql.SQL` composition (NOT f-string).

Replace the `cte = sql.SQL(...)` and `params = (...)` blocks (currently lines 891-922) with:

```python
    # CS-2: optional edge_kind AND-filter. None ⇒ no extra predicate
    # (byte-identical with pre-CS-2 behavior); a value ⇒ the recursive CTE's
    # anchor and recursive arms both require edge_kind = %s.
    if edge_kind is not None:
        kind_pred = sql.SQL(" AND edge_kind = %s")
        anchor_kind_params = (edge_kind,)
        recurse_kind_params = (edge_kind,)
    else:
        kind_pred = sql.SQL("")
        anchor_kind_params = ()
        recurse_kind_params = ()

    cte = sql.SQL(
        "WITH RECURSIVE walk AS ("
        " SELECT {next_col} AS fqn, {next_id_col} AS node_id, edge_type, confidence,"
        "        evidence, src_fqn, dst_fqn, 1::int AS hop"
        " FROM {fq}"
        " WHERE project_id = %s AND edge_type = %s AND {anchor_col} = %s"
        "       AND confidence >= %s{kind_pred}"
        " UNION ALL"
        " SELECT e.{next_col} AS fqn, e.{next_id_col} AS node_id, e.edge_type,"
        "        e.confidence, e.evidence, e.src_fqn, e.dst_fqn, w.hop + 1"
        " FROM {fq} e"
        " JOIN walk w ON w.fqn = e.{anchor_col}"
        " WHERE e.project_id = %s AND e.edge_type = %s AND e.confidence >= %s"
        "       AND w.hop < {depth}{kind_pred}"
        ")"
        " SELECT fqn, MIN(hop) AS hop, MAX(confidence) AS confidence,"
        " (ARRAY_AGG(evidence ORDER BY confidence DESC))[1] AS evidence,"
        " (ARRAY_AGG(src_fqn ORDER BY confidence DESC))[1] AS src_fqn,"
        " (ARRAY_AGG(dst_fqn ORDER BY confidence DESC))[1] AS dst_fqn"
        " FROM walk GROUP BY fqn ORDER BY hop ASC, confidence DESC"
    ).format(
        fq=fq,
        next_col=sql.Identifier(next_col),
        next_id_col=sql.Identifier(next_id_col),
        anchor_col=sql.Identifier(anchor_col),
        depth=sql.Literal(int(depth)),
        kind_pred=kind_pred,
    )

    params = (
        project_id, edge_type, fqn, confidence_floor, *anchor_kind_params,
        project_id, edge_type, confidence_floor, *recurse_kind_params,
    )
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_cli_impact_of.py -v`
Expected: all pass (new + existing).

- [ ] **Step 6: Commit**

```bash
git add python/src/chunkshop/cli.py python/tests/chunkshop/test_cli_impact_of.py
git commit -m "$(cat <<'EOF'
feat(cli): impact-of helper accepts optional edge_kind filter (CS-2)

_impact_query_one_direction now takes an optional edge_kind kwarg that
ANDs into both the anchor and recursive WHERE clauses. None preserves
byte-identical pre-CS-2 behavior; a value scopes the walk to a single
codegraph EdgeKind. Click-option wiring lands in the next commit.
EOF
)"
```

---

## Task 6: Add `--edge-kind` click option to `impact-of`

**Brief criteria:** SC-004

**Files:**
- Modify: `python/src/chunkshop/cli.py:1068-1203` (the `@cli.command(name="impact-of")` decorator stack + the `impact_of` function body)
- Modify: `python/tests/chunkshop/test_cli_impact_of.py` (add CLI-level tests using `click.testing.CliRunner`)

- [ ] **Step 1: Write the failing CLI-level tests**

Append to `python/tests/chunkshop/test_cli_impact_of.py`:

```python
def test_impact_of_cli_rejects_invalid_edge_kind() -> None:
    """--edge-kind validates against EDGE_KINDS at click parse time."""
    from click.testing import CliRunner

    from chunkshop.cli import cli

    runner = CliRunner()
    # No --config / --fqn needed — click validation runs before invocation.
    result = runner.invoke(
        cli,
        ["impact-of", "--config", "/dev/null", "--fqn", "x", "--edge-kind", "bogus_kind"],
    )
    assert result.exit_code != 0
    # Click's Choice error mentions valid values.
    assert "edge-kind" in result.output.lower() or "edge_kind" in result.output.lower()


def test_impact_of_cli_accepts_each_codegraph_edge_kind() -> None:
    """Every value in EDGE_KINDS passes click validation (may fail later on --config)."""
    from click.testing import CliRunner

    from chunkshop.cli import cli
    from chunkshop.extractors.code_relationships import EDGE_KINDS

    runner = CliRunner()
    for kind in EDGE_KINDS:
        result = runner.invoke(
            cli,
            ["impact-of", "--config", "/dev/null", "--fqn", "x", "--edge-kind", kind],
        )
        # Will fail on --config validation, but NOT on --edge-kind validation.
        assert "edge-kind" not in result.output.lower() or "invalid" not in result.output.lower(), (
            f"--edge-kind {kind} unexpectedly rejected: {result.output}"
        )
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_cli_impact_of.py -v -k cli_rejects -v` and `-k cli_accepts_each`
Expected: failures — `--edge-kind` option doesn't exist.

- [ ] **Step 3: Add the click option + plumbing**

In `python/src/chunkshop/cli.py`, in the `@cli.command(name="impact-of")` decorator stack, add a new option immediately after `--edge-type` (after current line 1091):

```python
@click.option(
    "--edge-kind",
    type=click.Choice([
        "contains", "calls", "imports", "exports",
        "extends", "implements", "references",
        "type_of", "returns", "instantiates",
        "overrides", "decorates",
    ]),
    default=None,
    help=(
        "Optional typed EdgeKind filter (codegraph ontology). ANDs with "
        "--edge-type when both are supplied. Today the extractor populates "
        "only 'calls', 'extends', 'implements'; CS-1 will fill the rest."
    ),
)
```

(Inline the 12-value tuple in the `Choice` rather than importing `EDGE_KINDS` — click decorators are evaluated at module-import time and importing a sibling-module constant at decorator-evaluation time can cause circular-import surprises in click's CLI tree. Keep it literal.)

Update the `impact_of` function signature to accept `edge_kind`:

```python
def impact_of(config, fqn, depth, direction, edge_type, edge_kind, confidence_floor, project_id, as_json):
```

(Order matters — match the click decorator order. `edge_kind` comes right after `edge_type`.)

Update both `_impact_query_one_direction(...)` call sites (currently lines 1155 and 1169) to pass `edge_kind=edge_kind`:

```python
            callers = _impact_query_one_direction(
                dsn,
                schema=schema,
                fqn=fqn,
                direction="callers",
                depth=depth,
                project_id=pid,
                confidence_floor=confidence_floor,
                edge_type=edge_type,
                edge_kind=edge_kind,
            )
```

(Same for callees block.)

Update the JSON output dict (currently around line 1188) to include `edge_kind`:

```python
        out: dict = {
            "target": fqn,
            "project_id": pid,
            "depth": depth,
            "direction": direction,
            "edge_type": edge_type,
            "edge_kind": edge_kind,
            "confidence_floor": confidence_floor,
        }
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd python && uv run --no-sync pytest tests/chunkshop/test_cli_impact_of.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/cli.py python/tests/chunkshop/test_cli_impact_of.py
git commit -m "$(cat <<'EOF'
feat(cli): impact-of --edge-kind option for typed EdgeKind filter (CS-2)

Click validates against the 12-value codegraph EdgeKind set at parse
time; the value (or None) flows through to _impact_query_one_direction
which ANDs it into the WHERE clauses. JSON output includes edge_kind
for round-trip clarity. AND semantics with --edge-type confirmed in
SC-004 tests.
EOF
)"
```

- [ ] **Step 6: ⛔ Drift Checkpoint DC-003**

Re-read `skill-output/mission-brief/Mission-Brief-cs2-typed-edge-kind.md`. Verify:
- SC-004: `--edge-kind` validates at click parse time? ✓ `click.Choice([...])` enforces this.
- SC-004: AND semantics confirmed? ✓ — both `--edge-type` and `--edge-kind` predicates appear in the WHERE; mismatched values return 0 rows (test in Task 5).
- Did I add an OR-mode flag, special `--edge-kind-only`, or any other option not in the brief? Should be **NO**. If yes, remove it — Out of Scope.

If any check fails, stop and fix before proceeding to Task 7.

---

## Task 7: CHANGELOG entry + ⛔ DC-FINAL

**Brief criteria:** SC-007, SC-008, SC-FINAL (all SCs verified)

**Files:**
- Modify: `CHANGELOG.md` (insert under `## Unreleased`)

- [ ] **Step 1: Add CHANGELOG entry**

In `CHANGELOG.md`, find the `## Unreleased` section (line 3). If it's empty (just the heading), add:

```markdown
## Unreleased

### Added

- **`code_relationships` extractor: typed `edge_kind` column on `code_edges` (CS-2).** The PG `code_edges` table now carries a typed, codegraph-aligned `edge_kind` column (12-value `CHECK` constraint: `contains`, `calls`, `imports`, `exports`, `extends`, `implements`, `references`, `type_of`, `returns`, `instantiates`, `overrides`, `decorates`) alongside the existing uppercase `edge_type` column. Today's three emission paths (`CALLS`, `INHERITS`, `IMPLEMENTS`) map to `calls`, `extends`, `implements`; the other nine values are valid against the constraint but unfilled until CS-1 ports the 20-language extractor stack. `chunkshop.extractors.code_relationships` exposes `EdgeKind` (Literal), `EDGE_KINDS` (tuple), and `edge_type_to_kind()` as the source-of-truth for the ontology.
- **`chunkshop impact-of --edge-kind <kind>` filter.** New CLI option validated against the 12-value EdgeKind set; ANDs into the recursive-CTE WHERE alongside the existing `--edge-type`. `--edge-kind` is `None` by default — pre-CS-2 invocations are byte-identical.

### Notes

- `edge_type` is unchanged: same column name, same uppercase values, same primary-key membership, same write semantics. Existing readers (`chunkshop impact-of --edge-type`, `pg-raggraph` consumers, `pg-raggraph/tests/integration/test_chunkshop_bridge.py`) continue working untouched.
- Cross-backend extension (MariaDB / SQLite / ClickHouse) is a separate follow-up brief blocked by a backend-agnostic `code_edges` DDL refactor — see `skill-output/mission-brief/Mission-Brief-cs2-cross-backend.md`.
- Rust parity is a separate follow-up brief — see `skill-output/mission-brief/Mission-Brief-cs2-rust-parity.md`.
```

- [ ] **Step 2: ⛔ Drift Checkpoint DC-FINAL — verify every SC**

Re-read `skill-output/mission-brief/Mission-Brief-cs2-typed-edge-kind.md`. For each SC, name the artifact that proves it:

- **SC-001** (every row has non-NULL valid `edge_kind`; CHECK rejects others): `test_code_edges_typed.py::test_edge_kind_check_constraint_rejects_invalid_value` + `test_write_edges_round_trip_persists_edge_kind`. ✓
- **SC-002** (exact mapping for 3 known values): `test_edge_kind_types.py::test_edge_type_to_kind_mapping` + `test_code_relationships_extractor.py::test_finalize_emits_correct_edge_kind_for_inherits_and_implements`. ✓
- **SC-003** (`edge_type` byte-identical): `test_code_edges_typed.py::test_schema_includes_edge_kind_column_with_check_constraint` (asserts edge_type column still present + NOT NULL + text). Also full pre-existing extractor suite still green. ✓
- **SC-004** (`--edge-kind` filter, AND semantics, rejects invalid): `test_cli_impact_of.py::test_impact_of_cli_rejects_invalid_edge_kind` + `test_impact_query_filters_by_edge_kind_when_supplied`. ✓
- **SC-005** (importable Literal + tuple, mypy-narrowable): `test_edge_kind_types.py::test_edge_kind_literal_is_importable` + `test_edge_kinds_tuple_is_codegraph_canonical_set`. ✓
- **SC-006** (existing extractor tests unchanged): inspect `git diff main -- python/tests/chunkshop/test_code_relationships_extractor.py python/tests/chunkshop/test_runner_finalize_wires_edges.py` — every change is additive (new test functions; no modifications to existing `assert` lines). ✓
- **SC-007** (two follow-up briefs on disk): `ls skill-output/mission-brief/Mission-Brief-cs2-*.md` shows `cs2-cross-backend.md` and `cs2-rust-parity.md`. ✓
- **SC-008** (CHANGELOG entry under `[Unreleased] → Added`): grep `CHANGELOG.md` for "edge_kind". ✓

Also run the full chunkshop test suite to catch any unintended regression:

```bash
cd python && uv run --no-sync pytest tests/chunkshop/ -x --timeout=60
```

Expected: all green (PG-backed tests skip cleanly if docker-compose stack isn't up).

If any SC lacks evidence, the work is **NOT complete** — fix the gap, then re-run DC-FINAL.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(changelog): add CS-2 typed edge_kind entry under [Unreleased]
EOF
)"
```

---

## Task 8: Create worktree, push, open PR

**Brief criteria:** none directly (handoff)

- [ ] **Step 1: If not already in a CS-2 worktree, create one**

```bash
git worktree list   # check if a CS-2 worktree already exists
# If not:
git worktree add ../chunkshop-cs2 -b feat/cs2-typed-edge-kind origin/main
cd ../chunkshop-cs2
git cherry-pick <commit-range>   # or do all the work directly here from the start
```

(If you started this plan directly on `main`, switch to that pattern earlier — ideally make the worktree before Task 1. The plan is structured so all changes are PG-scoped and won't conflict with parallel main work, but a worktree keeps the diff clean for review.)

- [ ] **Step 2: Push the branch**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs2   # or wherever the worktree is
git push -u origin feat/cs2-typed-edge-kind
```

- [ ] **Step 3: Open PR**

```bash
gh pr create --base main --head feat/cs2-typed-edge-kind \
  --title "feat(code_edges): CS-2 typed edge_kind column + --edge-kind filter" \
  --body "$(cat <<'EOF'
## Summary
- Adds a typed, codegraph-aligned `edge_kind` column to the PG `code_edges` table (12-value CHECK constraint).
- Maps the 3 existing uppercase `edge_type` values: `CALLS→calls`, `INHERITS→extends`, `IMPLEMENTS→implements`. Other 9 codegraph values are valid against the constraint but unfilled until CS-1.
- New `chunkshop impact-of --edge-kind <kind>` option, ANDs with `--edge-type` when both supplied.
- `edge_type` column is **untouched** — name, type, PK membership, values all byte-identical. `chunkshop impact-of --edge-type CALLS`, `pg-raggraph`'s consumers, and `pg-raggraph/tests/integration/test_chunkshop_bridge.py` keep working with zero changes.
- Heads-up for pg-raggraph: `test_chunkshop_bridge.py` creates its own stand-in `code_edges` schema and will silently lack the new `edge_kind` column. Update on your schedule — not blocking.

## Mission brief
`skill-output/mission-brief/Mission-Brief-cs2-typed-edge-kind.md` (gitignored). Two follow-up briefs filed for cross-backend (MariaDB/SQLite/ClickHouse) and Rust parity.

## Test plan
- [ ] `cd python && uv run --no-sync pytest tests/chunkshop/test_edge_kind_types.py -v`
- [ ] `cd python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py -v` (incl. 2 new additive tests)
- [ ] `cd python && uv run --no-sync pytest tests/chunkshop/test_cli_impact_of.py -v` (incl. new CLI option tests)
- [ ] With docker-compose.test.yaml up: `cd python && uv run --no-sync pytest tests/chunkshop/test_code_edges_typed.py -v` (5 PG-backed tests)
- [ ] Smoke-test live: run any factorial-int8 cell + inspect `SELECT edge_type, edge_kind, COUNT(*) FROM code_edges GROUP BY 1,2`; expect `(CALLS, calls)`, `(INHERITS, extends)`, `(IMPLEMENTS, implements)` triples with non-zero counts.

## Out of scope (deferred to follow-up briefs)
- Sink files (`sinks/mariadb.py`, `sinks/sqlite.py`, `sinks/clickhouse.py`) — see `Mission-Brief-cs2-cross-backend.md`.
- Rust port (`rust/chunkshop/src/extractors/` doesn't exist yet) — see `Mission-Brief-cs2-rust-parity.md`.
- CS-5 provenance columns.
- pg-raggraph cross-repo patches.
EOF
)"
```

- [ ] **Step 4: Verify CI is green**

After the PR opens, watch the CI run. If any test fails, fix in-place (additional commits on the branch); do not merge a red PR.

---

## Self-review (run before declaring the plan ready)

- **Spec coverage:** Every SC-001 through SC-008 has a task that produces evidence for it (mapped in DC-FINAL). ✓
- **Drift checkpoints:** DC-001 (Task 2 step 6), DC-002 (Task 4 step 6), DC-003 (Task 6 step 6), DC-FINAL (Task 7 step 2) — all four present as ⛔ hard gates. ✓
- **Out-of-scope discipline:** Every task's "Files" section lists only `code_relationships.py`, `cli.py`, test files under `tests/chunkshop/`, and `CHANGELOG.md`. No `sinks/`, no `rust/`, no `pg-raggraph` paths. ✓
- **Type consistency:** `EdgeKind`, `EDGE_KINDS`, `edge_type_to_kind` names match across Tasks 1, 3, 4, 5, 6. ✓
- **No placeholders:** Task 5 step 2 leaves CliRunner-fixture stubs `...` for the engineer to fill — this is the only intentional placeholder, and it's clearly bracketed as "match the file's existing fixture pattern" (since fixture style varies test-file-to-test-file in this codebase). All other code blocks are complete.
- **TDD discipline:** every task is failing-test → minimal-impl → passing-test → commit. ✓
