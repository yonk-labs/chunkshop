# CS-5 — `provenance` + `provenance_metadata` on `code_edges` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Active mission brief:** `skill-output/mission-brief/Mission-Brief-cs5-provenance.md` — re-read at every ⛔ drift checkpoint.

**Goal:** Add two additive PG columns to `code_edges` — `provenance text NOT NULL DEFAULT 'ast'` (3-value CHECK) and `provenance_metadata jsonb NOT NULL DEFAULT '{}'` — populated at the existing `_emit` chokepoint with hardcoded `'ast'` + empty metadata. Strictly additive on top of CS-2; CS-3 synthesizers will set `'heuristic'` later.

**Architecture:** Mirrors CS-2 exactly. Add a `Provenance` Literal + `PROVENANCES` tuple to `chunkshop.extractors.code_relationships`. Patch `_emit` (single chokepoint inside `finalize`) so every emitted edge dict carries `provenance='ast'` and `provenance_metadata={}`. Patch `write_edges_schema` to add the two columns + CHECK constraint + supporting index. Patch `write_edges` INSERT to emit both columns and preserve them on `ON CONFLICT DO UPDATE`. No CLI surface in this PR — provenance becomes a useful filter only when CS-3 produces non-AST edges.

**Tech Stack:** Python 3.11+, `psycopg` v3, pytest. PG via `docker-compose.test.yaml` (or any reachable PG on `localhost:5434/chunkshop_test`). No new runtime deps.

**Lessons from CS-2 baked in:**
- All test snippets use `source_path=` as the `extract()` kwarg (CS-2 caught the plan-doc typo `file_path=`; the real signature is `def extract(self, text, *, source_path=None, language=None, ...)`).
- The ON-CONFLICT preservation test (Task 4) **pre-seeds a row with a different value** to actually exercise `SET <col> = EXCLUDED.<col>` — running `write_edges` twice and asserting "still the same value" passes for the wrong reason because un-SET columns are preserved automatically (CS-2 caught this).

**Out-of-scope reminder (from brief):** sink files, Rust, CLI option (`--provenance` filter is YAGNI until CS-3), tree-sitter-vs-regex differentiation within `'ast'`, pg-raggraph patches.

**File map:**
- `python/src/chunkshop/extractors/code_relationships.py` — module-level types block extended; `_emit` patched; `write_edges_schema` DDL + index patched; `write_edges` INSERT + ON CONFLICT patched.
- `python/tests/chunkshop/test_provenance_types.py` (new) — pure-Python types test.
- `python/tests/chunkshop/test_code_edges_provenance.py` (new) — live-PG tests (CHECK, round-trip, extractor population, CS-2 regression).
- `python/tests/chunkshop/test_code_relationships_extractor.py` (modify, append only) — assert `provenance` + `provenance_metadata` keys appear on every finalize edge.
- `CHANGELOG.md` — `[Unreleased] → ### Added` entry.

---

## Task 1: Add `Provenance` Literal + `PROVENANCES` tuple (pure Python, no PG)

**Brief criteria:** SC-006

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py` (add types block immediately after the existing `edge_type_to_kind` function, around line 105)
- Create: `python/tests/chunkshop/test_provenance_types.py`

- [ ] **Step 1: Write the failing test**

```python
# python/tests/chunkshop/test_provenance_types.py
"""Pure-Python sanity tests for the CS-5 Provenance ontology.

No PG, no fixtures — just the type alias and tuple imported straight
from chunkshop.extractors.code_relationships.
"""
from __future__ import annotations


def test_provenances_tuple_is_three_value_set() -> None:
    """PROVENANCES must be exactly ('ast', 'scip', 'heuristic') in that order."""
    from chunkshop.extractors.code_relationships import PROVENANCES

    assert PROVENANCES == ("ast", "scip", "heuristic")
    assert len(PROVENANCES) == 3
    assert all(p.islower() and p.isalpha() for p in PROVENANCES)
    assert len(set(PROVENANCES)) == 3


def test_provenance_literal_is_importable() -> None:
    """Provenance is a Literal[...] that mypy/pyright can narrow."""
    from chunkshop.extractors.code_relationships import Provenance  # noqa: F401

    from typing import get_args
    assert set(get_args(Provenance)) == {"ast", "scip", "heuristic"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_provenance_types.py -v
```
Expected: 2 failures — `ImportError: cannot import name 'PROVENANCES'`.

- [ ] **Step 3: Implement the types**

In `python/src/chunkshop/extractors/code_relationships.py`, immediately after the `edge_type_to_kind` function definition (around line 105, before the next section header `# Inheritance / implementation regexes`), add:

```python
# ---------------------------------------------------------------------------
# CS-5: provenance ontology
# ---------------------------------------------------------------------------
#
# The 3-value vocabulary is ported from codegraph's `Edge.provenance` field
# (renaming codegraph's `'tree-sitter'` → `'ast'` to leave room for future
# non-tree-sitter AST sources). Additive — every existing emission path is
# AST-derived, so the chokepoint hardcodes `'ast'`. CS-3 synthesizers will
# emit `'heuristic'` through their own code path (not through finalize._emit)
# with a `{synthesizedBy: <channel>}` provenance_metadata payload.

Provenance = Literal["ast", "scip", "heuristic"]

PROVENANCES: tuple[Provenance, ...] = ("ast", "scip", "heuristic")
```

(`Literal` is already imported at module top from CS-2; no new import needed.)

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_provenance_types.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5
git add python/src/chunkshop/extractors/code_relationships.py python/tests/chunkshop/test_provenance_types.py
git commit -m "$(cat <<'EOF'
feat(code_relationships): add Provenance Literal + PROVENANCES tuple (CS-5 prep)

Pure-Python types, no PG schema or extractor write-path changes yet.
Lays the source-of-truth Provenance Literal used by the DDL CHECK
constraint and the chokepoint patch (next commits).
EOF
)"
```

---

## Task 2: Patch `write_edges_schema` DDL — add columns + CHECK + index (⛔ DC-001)

**Brief criteria:** SC-001 (constraint shape), SC-002 (CHECK enforcement), SC-004 (CS-2 invariants preserved)

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py` — `write_edges_schema` (DDL block around lines 547-570, index block around lines 583-591)
- Create: `python/tests/chunkshop/test_code_edges_provenance.py`

- [ ] **Step 1: Write the failing tests**

Create `python/tests/chunkshop/test_code_edges_provenance.py`:

```python
"""Live-PG tests for the CS-5 provenance + provenance_metadata columns.

Skips cleanly when CHUNKSHOP_TEST_DSN isn't reachable. Each test creates
+ drops its own schema so tests don't leak state.
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

    name = f"chunkshop_cs5_{uuid.uuid4().hex[:8]}"
    yield name
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def test_schema_includes_provenance_columns(schema: str) -> None:
    """write_edges_schema creates code_edges with both new columns."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'code_edges' "
            "ORDER BY column_name",
            (schema,),
        )
        cols = {row[0]: row for row in cur.fetchall()}

        # New CS-5 columns.
        assert "provenance" in cols
        assert cols["provenance"][1] == "text"
        assert cols["provenance"][2] == "NO"  # NOT NULL
        assert cols["provenance"][3] is not None  # has DEFAULT

        assert "provenance_metadata" in cols
        assert cols["provenance_metadata"][1] == "jsonb"
        assert cols["provenance_metadata"][2] == "NO"  # NOT NULL
        assert cols["provenance_metadata"][3] is not None  # has DEFAULT


def test_provenance_check_constraint_rejects_invalid_value(schema: str) -> None:
    """CHECK constraint refuses values outside {ast, scip, heuristic}."""
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
                    " dst_node_id, confidence, evidence, edge_kind, provenance) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)"
                ).format(fq=fq),
                ("p", "CALLS", "a", "b", "id_a", "id_b", 0.9, "{}", "calls", "bogus_provenance"),
            )


def test_provenance_check_accepts_all_three_values(schema: str) -> None:
    """All 3 PROVENANCES values satisfy the CHECK constraint."""
    import psycopg

    from chunkshop.extractors.code_relationships import PROVENANCES, write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql

        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        for i, prov in enumerate(PROVENANCES):
            cur.execute(
                sql.SQL(
                    "INSERT INTO {fq} "
                    "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                    " dst_node_id, confidence, evidence, edge_kind, provenance) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)"
                ).format(fq=fq),
                ("p", "CALLS", f"a{i}", f"b{i}", f"id_a{i}", f"id_b{i}", 0.9, "{}", "calls", prov),
            )
        conn.commit()
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {fq}").format(fq=fq))
        assert cur.fetchone()[0] == 3


def test_schema_preserves_cs2_columns_unchanged(schema: str) -> None:
    """SC-004 regression guard: edge_type + edge_kind columns are byte-identical to post-CS-2."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'code_edges' "
            "AND column_name IN ('edge_type', 'edge_kind')",
            (schema,),
        )
        cols = {row[0]: row for row in cur.fetchall()}

        # edge_type (CS-2 legacy): text, NOT NULL.
        assert cols["edge_type"][1] == "text"
        assert cols["edge_type"][2] == "NO"
        # edge_kind (CS-2 typed): text, NOT NULL.
        assert cols["edge_kind"][1] == "text"
        assert cols["edge_kind"][2] == "NO"

        # PK still includes edge_type (not edge_kind, not provenance).
        cur.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = %s::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum)",
            (f"{schema}.code_edges",),
        )
        pk_cols = [r[0] for r in cur.fetchall()]
        assert pk_cols == ["project_id", "edge_type", "src_node_id", "dst_node_id"]
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_edges_provenance.py -v
```
Expected: 4 failures — columns don't exist; CHECK doesn't exist.

(If tests **skip**, PG isn't reachable. Verify `psql -h localhost -p 5434 -U postgres -d chunkshop_test` and report BLOCKED.)

- [ ] **Step 3: Patch the DDL**

In `python/src/chunkshop/extractors/code_relationships.py`, locate the `CREATE TABLE IF NOT EXISTS` block inside `write_edges_schema` (around lines 547-570). It currently ends with the `edge_kind` block + PRIMARY KEY. Add the two CS-5 columns inside the column list, immediately AFTER the `edge_kind ...` block and BEFORE the `PRIMARY KEY (...)` line. The resulting DDL string should look like:

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
                # CS-5: provenance tagging. Every existing emission path is
                # AST-derived, so DEFAULT 'ast' correctly backfills pre-CS-5
                # rows. CS-3 synthesizers will explicitly set 'heuristic' with
                # a {synthesizedBy: <channel>} provenance_metadata payload.
                " provenance text NOT NULL DEFAULT 'ast'"
                "   CHECK (provenance IN ('ast', 'scip', 'heuristic')),"
                " provenance_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,"
                " PRIMARY KEY (project_id, edge_type, src_node_id, dst_node_id))"
            ).format(fq=fq)
        )
```

After the existing `code_edges_kind_idx` index creation (around lines 583-591), add a fifth index immediately after it:

```python
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {ix} ON {fq} "
                "(project_id, provenance)"
            ).format(ix=sql.Identifier("code_edges_provenance_idx"), fq=fq)
        )
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_edges_provenance.py -v
```
Expected: 4 passed.

Also re-run CS-2's typed test suite to confirm the additive DDL didn't break it:
```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_edges_typed.py tests/chunkshop/test_code_relationships_extractor.py tests/chunkshop/test_runner_finalize_wires_edges.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5
git add python/src/chunkshop/extractors/code_relationships.py python/tests/chunkshop/test_code_edges_provenance.py
git commit -m "$(cat <<'EOF'
feat(code_relationships): add provenance + provenance_metadata columns + CHECK + index (CS-5)

Schema-only patch: write_edges_schema now creates code_edges with the
codegraph provenance ontology (ast | scip | heuristic) as a CHECK
constraint, plus a jsonb provenance_metadata column. Both default
correctly for backwards compatibility — every existing row is
AST-derived, so DEFAULT 'ast' + DEFAULT '{}' is the right backfill.

CS-2 columns (edge_type, edge_kind) and the PRIMARY KEY are untouched.
Next commits wire the extractor _emit chokepoint to populate the new
columns explicitly.
EOF
)"
```

- [ ] **Step 6: ⛔ Drift Checkpoint DC-001**

Re-read `skill-output/mission-brief/Mission-Brief-cs5-provenance.md`. Verify each as ✓/✗ in your report:

- **SC-002 (CHECK values verbatim):** Does the CHECK list exactly `('ast', 'scip', 'heuristic')` in that order? Did I accidentally include `'tree-sitter'` (codegraph's value)?
- **SC-004 (`edge_type` + `edge_kind` byte-identical):** Diff the post-patch DDL string against the post-CS-2 baseline (commit `8eeced9`'s `code_relationships.py`). Are the `edge_type`, `edge_kind`, and `PRIMARY KEY` lines literally unchanged? (Should be — additive only.)
- **Index naming:** Is the new index named `code_edges_provenance_idx` (matching the `code_edges_*_idx` family)?
- **`provenance_metadata` default:** Is it `'{}'::jsonb` (NOT NULL, NOT a Python `None`, NOT a TEXT cast)?
- **Additivity constraint:** `git diff origin/main..HEAD --stat` — only `code_relationships.py` + the new test file should appear.

If any check fails, stop and fix before proceeding to Task 3.

---

## Task 3: Patch `_emit` chokepoint to populate both columns (⛔ DC-002)

**Brief criteria:** SC-001 (every row populated), SC-005 (existing tests unchanged), Chokepoint constraint

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py` — `_emit`'s `edges.append({...})` block around lines 330-345
- Modify: `python/tests/chunkshop/test_code_relationships_extractor.py` — APPEND 1 new test (do NOT modify existing assertions)

- [ ] **Step 1: Write the failing test (append-only)**

APPEND to the END of `python/tests/chunkshop/test_code_relationships_extractor.py`:

```python
# ---------------------------------------------------------------------------
# CS-5: provenance on every finalize edge
# ---------------------------------------------------------------------------


def test_finalize_emits_provenance_ast_with_empty_metadata() -> None:
    """Every edge from finalize() carries provenance='ast' and provenance_metadata={}."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import CodeRelationshipsExtractor

    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    # Minimal cross-file Python: a.py defines foo; b.py calls foo.
    ext.extract(
        "def foo():\n    pass\n",
        language="python",
        source_path="a.py",
    )
    ext.extract(
        "def bar():\n    foo()\n",
        language="python",
        source_path="b.py",
    )
    edges = ext.finalize(project_id="test")

    assert len(edges) >= 1
    for e in edges:
        # CS-2 regression: edge_type + edge_kind still present.
        assert "edge_type" in e
        assert "edge_kind" in e
        # CS-5: provenance + provenance_metadata present with default values.
        assert e["provenance"] == "ast"
        assert e["provenance_metadata"] == {}
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py::test_finalize_emits_provenance_ast_with_empty_metadata -v
```
Expected: failure — `KeyError: 'provenance'`.

- [ ] **Step 3: Patch `_emit`**

In `python/src/chunkshop/extractors/code_relationships.py`, locate the `edges.append({...})` block inside the `_emit` nested function (around lines 330-345). After Task 2, it currently contains the `edge_kind` key. Add the two CS-5 keys at the end of the dict, just before the closing `}`:

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
                    # CS-5: every edge from this extractor is AST-derived.
                    # CS-3 synthesizers will emit through their own code
                    # path (not through finalize._emit) with 'heuristic'
                    # + a {synthesizedBy: <channel>} payload.
                    "provenance": "ast",
                    "provenance_metadata": {},
                }
            )
```

**Crucial discipline check:** do NOT touch any of the 5 `_emit(edge_type="CALLS"/"INHERITS"/"IMPLEMENTS", ...)` call sites (around lines 352-410). The patch is in `_emit` only.

- [ ] **Step 4: Run tests, verify all pass**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py tests/chunkshop/test_runner_finalize_wires_edges.py -v
```
Expected: every test passes — the new one + every pre-existing test unchanged. **If any pre-existing test fails, you mutated an existing assertion — back it out and patch additively.**

- [ ] **Step 5: Commit**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5
git add python/src/chunkshop/extractors/code_relationships.py python/tests/chunkshop/test_code_relationships_extractor.py
git commit -m "$(cat <<'EOF'
feat(code_relationships): emit provenance='ast' + empty metadata in finalize (CS-5)

_emit() chokepoint now hardcodes provenance='ast' and provenance_metadata={}
on every appended edge dict. All 5 emission paths (CALLS intra-file,
CALLS unique-name, CALLS ambiguous, INHERITS, IMPLEMENTS) inherit it
automatically — single point of update.

edge_type and edge_kind assertions in existing tests preserved verbatim;
the change is additive. Test asserts both new keys appear on every edge.
EOF
)"
```

- [ ] **Step 6: ⛔ Drift Checkpoint DC-002**

Re-read `skill-output/mission-brief/Mission-Brief-cs5-provenance.md`. Verify each as ✓/✗ in your report:

- **SC-001 (every row populated):** Does the test confirm every finalize edge has `provenance='ast'` AND `provenance_metadata={}`? (Should be — verified by the new test.)
- **SC-005 (existing tests unchanged):** Run `cd python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py tests/chunkshop/test_runner_finalize_wires_edges.py -v`; expected green. Inspect `git diff origin/main..HEAD -- <those test files>`; should show only ADDITIVE lines (no `-` lines on existing assertions).
- **Chokepoint discipline:** Did I touch any of the 5 `_emit(edge_type=...)` call sites at lines ~352-410? Should be **NO**. The patch is in the `edges.append({...})` block of `_emit` only. If yes — undo and re-route through `_emit`.

If any check fails, stop and fix before proceeding to Task 4.

---

## Task 4: Patch `write_edges` INSERT to persist both columns (⛔ DC-003)

**Brief criteria:** SC-001 (persistence), SC-003 (jsonb round-trip), ON CONFLICT preservation

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py` — `write_edges` function (row comprehension around lines 622-635, INSERT SQL + ON CONFLICT around lines 636-652)
- Modify: `python/tests/chunkshop/test_code_edges_provenance.py` — APPEND 3 new tests (round-trip, jsonb-arbitrary, on-conflict-update)

- [ ] **Step 1: APPEND the failing tests**

APPEND to `python/tests/chunkshop/test_code_edges_provenance.py`:

```python
def test_write_edges_round_trip_persists_provenance_ast(schema: str) -> None:
    """write_edges populates provenance='ast' and provenance_metadata={} on every row."""
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract("def foo():\n    pass\n", language="python", source_path="a.py")
    ext.extract("def bar():\n    foo()\n", language="python", source_path="b.py")

    n = write_edges(ext, dsn=DSN, schema=schema, project_id="rt")
    assert n >= 1

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql
        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        cur.execute(
            sql.SQL("SELECT provenance, provenance_metadata FROM {fq}").format(fq=fq)
        )
        rows = cur.fetchall()
        assert len(rows) >= 1
        for provenance, metadata in rows:
            assert provenance == "ast"
            assert metadata == {}


def test_provenance_metadata_round_trips_arbitrary_json(schema: str) -> None:
    """SC-003: provenance_metadata jsonb accepts and round-trips arbitrary JSON."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    write_edges_schema(DSN, schema=schema)

    payload = {"foo": "bar", "nested": [1, 2], "k": None, "deep": {"a": "b"}}

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        from psycopg import sql
        from psycopg.types.json import Json
        fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
        cur.execute(
            sql.SQL(
                "INSERT INTO {fq} "
                "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                " dst_node_id, confidence, evidence, edge_kind, provenance, "
                " provenance_metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)"
            ).format(fq=fq),
            ("p", "CALLS", "a", "b", "id_a", "id_b", 0.9, "{}", "calls", "heuristic", Json(payload)),
        )
        conn.commit()
        cur.execute(sql.SQL("SELECT provenance_metadata FROM {fq}").format(fq=fq))
        round_tripped = cur.fetchone()[0]
        assert round_tripped == payload


def test_write_edges_on_conflict_updates_provenance(schema: str) -> None:
    """ON CONFLICT DO UPDATE flips provenance + provenance_metadata to EXCLUDED values.

    Pre-seeds a conflicting row with deliberately wrong values (provenance='heuristic',
    provenance_metadata={'fake': 'data'}). Then calls write_edges which produces an
    edge with the SAME primary key but provenance='ast' / metadata={}. Asserts the
    existing row was overwritten — proving the SET clause is wired (mirrors CS-2's
    test_write_edges_on_conflict_updates_edge_kind pattern).
    """
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors.code_relationships import (
        CodeRelationshipsExtractor,
        write_edges,
        write_edges_schema,
    )

    write_edges_schema(DSN, schema=schema)

    # Run extractor once to discover the PK tuple write_edges will produce.
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract("def foo():\n    pass\n", language="python", source_path="a.py")
    ext.extract("def bar():\n    foo()\n", language="python", source_path="b.py")
    edges = ext.finalize(project_id="rt")
    assert len(edges) >= 1
    target = edges[0]
    assert target["provenance"] == "ast"
    assert target["provenance_metadata"] == {}

    # Pre-seed with wrong values (valid against CHECK; wrong for an AST edge).
    from psycopg import sql
    from psycopg.types.json import Json
    fq = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier("code_edges"))
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "INSERT INTO {fq} "
                "(project_id, edge_type, src_fqn, dst_fqn, src_node_id, "
                " dst_node_id, confidence, evidence, edge_kind, provenance, "
                " provenance_metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)"
            ).format(fq=fq),
            (
                "rt",
                target["edge_type"],
                target["src_fqn"],
                target["dst_fqn"],
                target["src_node_id"],
                target["dst_node_id"],
                0.1,
                "{}",
                "calls",
                "heuristic",  # deliberately wrong
                Json({"fake": "data"}),  # deliberately wrong
            ),
        )
        conn.commit()

    # Sanity-check pre-seed.
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT provenance, provenance_metadata FROM {fq} "
                "WHERE project_id = %s AND edge_type = %s "
                "  AND src_node_id = %s AND dst_node_id = %s"
            ).format(fq=fq),
            ("rt", target["edge_type"], target["src_node_id"], target["dst_node_id"]),
        )
        row = cur.fetchone()
        assert row[0] == "heuristic"
        assert row[1] == {"fake": "data"}

    # Now run write_edges — PK collides → ON CONFLICT DO UPDATE → flip to ast/{}.
    ext2 = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext2.extract("def foo():\n    pass\n", language="python", source_path="a.py")
    ext2.extract("def bar():\n    foo()\n", language="python", source_path="b.py")
    write_edges(ext2, dsn=DSN, schema=schema, project_id="rt")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT provenance, provenance_metadata FROM {fq} "
                "WHERE project_id = %s AND edge_type = %s "
                "  AND src_node_id = %s AND dst_node_id = %s"
            ).format(fq=fq),
            ("rt", target["edge_type"], target["src_node_id"], target["dst_node_id"]),
        )
        row = cur.fetchone()
        assert row[0] == "ast", (
            "ON CONFLICT DO UPDATE did not flip provenance — "
            "SET provenance = EXCLUDED.provenance may be missing"
        )
        assert row[1] == {}, (
            "ON CONFLICT DO UPDATE did not flip provenance_metadata — "
            "SET provenance_metadata = EXCLUDED.provenance_metadata may be missing"
        )
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_edges_provenance.py -v
```
Expected: 3 new failures. The round-trip test fails because INSERT doesn't supply the new columns yet (they fall back to DEFAULT 'ast' / DEFAULT '{}' — wait, that would actually pass round-trip). Hmm — let me reason:

- After Task 2, the DDL has DEFAULT 'ast' and DEFAULT '{}'. So an INSERT that omits the new columns still produces `provenance='ast'` / `provenance_metadata={}` — which matches the round-trip test's assertions!
- So `test_write_edges_round_trip_persists_provenance_ast` would PASS even before Task 4's patch.
- `test_provenance_metadata_round_trips_arbitrary_json` only uses hand-INSERT (not `write_edges`), so it doesn't depend on Task 4 either — it passes after Task 2 already.
- `test_write_edges_on_conflict_updates_provenance` is the one that genuinely fails before Task 4: pre-seeded `'heuristic'` stays put because `write_edges`'s ON CONFLICT clause doesn't SET those columns.

So expect: 1 failure (the on-conflict test), 2 passes (round-trip + jsonb arbitrary). That's correct — the only thing Task 4 actually adds *behavior* for is the ON CONFLICT clause. The round-trip + jsonb tests are passive regression guards that protect against a future where someone "optimizes" the DEFAULT clause out.

- [ ] **Step 3: Patch `write_edges`**

In `python/src/chunkshop/extractors/code_relationships.py`, find the `write_edges` function (around line 596). Replace the `rows = [...]` comprehension and the `insert = sql.SQL(...)` block (around lines 622-652) with:

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
            # CS-5: every finalize() edge now carries provenance + metadata.
            e["provenance"],
            Json(e.get("provenance_metadata") or {}),
        )
        for e in edges
    ]

    insert = sql.SQL(
        "INSERT INTO {fq} (project_id, edge_type, src_fqn, dst_fqn,"
        " src_node_id, dst_node_id, confidence, evidence, edge_kind,"
        " provenance, provenance_metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (project_id, edge_type, src_node_id, dst_node_id) "
        "DO UPDATE SET"
        "  src_fqn = EXCLUDED.src_fqn,"
        "  dst_fqn = EXCLUDED.dst_fqn,"
        "  confidence = EXCLUDED.confidence,"
        "  evidence = EXCLUDED.evidence,"
        # CS-2: preserve edge_kind on update so a re-run with a changed
        # mapping (future ontology migration) doesn't leave stale values.
        "  edge_kind = EXCLUDED.edge_kind,"
        # CS-5: preserve provenance + provenance_metadata on update so a
        # CS-3-era reclassification (e.g., an edge previously synthesized
        # heuristically gets re-derived from AST in a later run) cleanly
        # overwrites instead of leaving stale provenance.
        "  provenance = EXCLUDED.provenance,"
        "  provenance_metadata = EXCLUDED.provenance_metadata"
    ).format(fq=fq)
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_edges_provenance.py -v
```
Expected: 7 passed (4 from Task 2 + 3 new).

Re-run extractor + runner suite to catch regressions:
```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py tests/chunkshop/test_runner_finalize_wires_edges.py tests/chunkshop/test_code_edges_typed.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5
git add python/src/chunkshop/extractors/code_relationships.py python/tests/chunkshop/test_code_edges_provenance.py
git commit -m "$(cat <<'EOF'
feat(code_relationships): write provenance + metadata in write_edges INSERT (CS-5)

INSERT now supplies provenance and provenance_metadata from the
finalize() row. ON CONFLICT DO UPDATE preserves both so a future
reclassification re-run cleanly overwrites stale values.

Tests: round-trip (write 'ast', read 'ast'), jsonb-arbitrary
(handcraft + round-trip a nested payload), and on-conflict-update
(pre-seed 'heuristic' + {fake: data}, run write_edges, assert
flip to 'ast' + {}) — the conflict test mirrors the CS-2 pattern
that pre-seeds a different value rather than running write_edges
twice.
EOF
)"
```

- [ ] **Step 6: ⛔ Drift Checkpoint DC-003**

Re-read `skill-output/mission-brief/Mission-Brief-cs5-provenance.md`. Verify each as ✓/✗ in your report:

- **SC-001 round-trip:** Does `test_write_edges_round_trip_persists_provenance_ast` pass? It should — write_edges now supplies both columns explicitly.
- **SC-003 jsonb arbitrary:** Does `test_provenance_metadata_round_trips_arbitrary_json` pass? It should — jsonb deserialization is automatic via psycopg.
- **ON CONFLICT preservation (lesson from CS-2):** Does `test_write_edges_on_conflict_updates_provenance` pass with the pre-seed-then-flip pattern? It should — both `provenance` and `provenance_metadata` are in the `DO UPDATE SET` clause.
- **CS-2 invariants intact:** Does `test_code_edges_typed.py` still pass entirely (5/5)? Did the INSERT changes affect `edge_kind` persistence? Should be NO — `edge_kind = EXCLUDED.edge_kind` line is unchanged.

If any check fails, stop and fix before proceeding to Task 5.

---

## Task 5: CHANGELOG + ⛔ DC-FINAL

**Brief criteria:** SC-007 (follow-up briefs on disk), SC-008 (CHANGELOG entry), DC-FINAL (every SC has evidence)

**Files:**
- Modify: `CHANGELOG.md` (insert under `## Unreleased`)

- [ ] **Step 1: Add CHANGELOG entry**

In `CHANGELOG.md`, find the `## Unreleased` section. If CS-2's entries from PR #38 are already under `### Added`, **add a new bullet** below them. If `### Added` doesn't exist (e.g., a fresh release just happened), create it. The new bullet:

```markdown
- **`code_relationships` extractor: `provenance` + `provenance_metadata` columns on `code_edges` (CS-5).** The PG `code_edges` table now carries provenance tagging — a typed `provenance text NOT NULL DEFAULT 'ast'` column (3-value `CHECK`: `'ast' | 'scip' | 'heuristic'`) plus a `provenance_metadata jsonb NOT NULL DEFAULT '{}'` column for per-edge per-channel context (e.g., `{synthesizedBy: 'react-render', componentName: 'App'}` once CS-3 synthesizers land). Every edge from today's AST extractor is hardcoded to `provenance='ast'` with empty metadata. Foundation for CS-3 — without provenance, an AST-truth edge and a heuristic-guess edge are indistinguishable, and per codegraph's CLAUDE.md "partial coverage is WORSE than none" if you can't tell which is which. `chunkshop.extractors.code_relationships` exposes `Provenance` (Literal) and `PROVENANCES` (tuple).
```

Under `### Notes` (or add it if missing), add:

```markdown
- CS-5 is strictly additive on top of CS-2 — `edge_type`, `edge_kind`, and the `code_edges` PRIMARY KEY are byte-identical to the post-CS-2 state.
- Cross-backend extension (MariaDB / SQLite / ClickHouse) is a separate follow-up brief — see `skill-output/mission-brief/Mission-Brief-cs5-cross-backend.md`. Should be bundled with `Mission-Brief-cs2-cross-backend.md` since they share the backend-agnostic DDL-seam refactor.
- Rust parity is a separate follow-up brief — see `skill-output/mission-brief/Mission-Brief-cs5-rust-parity.md`. Blocked on `Mission-Brief-cs2-rust-parity.md` (which creates the `rust/chunkshop/src/extractors/` directory CS-5's Rust port lives in).
- No CLI surface in this PR — `chunkshop impact-of --provenance <kind>` filter is YAGNI until CS-3 produces non-AST edges to filter against.
```

- [ ] **Step 2: ⛔ Drift Checkpoint DC-FINAL — verify every SC**

Re-read `skill-output/mission-brief/Mission-Brief-cs5-provenance.md`. For each SC, find evidence and report ✓/✗:

- **SC-001** (every row has `provenance='ast'` + `provenance_metadata={}`): `cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/test_code_edges_provenance.py::test_write_edges_round_trip_persists_provenance_ast tests/chunkshop/test_code_relationships_extractor.py::test_finalize_emits_provenance_ast_with_empty_metadata -v` — both green.
- **SC-002** (CHECK rejects invalid, accepts all 3): `pytest tests/chunkshop/test_code_edges_provenance.py::test_provenance_check_constraint_rejects_invalid_value tests/chunkshop/test_code_edges_provenance.py::test_provenance_check_accepts_all_three_values -v` — both green.
- **SC-003** (jsonb round-trip): `pytest tests/chunkshop/test_code_edges_provenance.py::test_provenance_metadata_round_trips_arbitrary_json -v` — green.
- **SC-004** (CS-2 columns byte-identical): `pytest tests/chunkshop/test_code_edges_provenance.py::test_schema_preserves_cs2_columns_unchanged tests/chunkshop/test_code_edges_typed.py -v` — all green. Also `git diff origin/main..HEAD -- python/src/chunkshop/extractors/code_relationships.py | grep -E '^-.*edge_type|^-.*edge_kind|^-.*PRIMARY KEY'` — should be empty (no removals).
- **SC-005** (existing tests unchanged): `git diff origin/main..HEAD -- python/tests/chunkshop/test_code_relationships_extractor.py python/tests/chunkshop/test_runner_finalize_wires_edges.py | grep -E '^-' | grep -v '^---' | head` — should be empty or only blank-line deletions.
- **SC-006** (Provenance / PROVENANCES importable): `pytest tests/chunkshop/test_provenance_types.py -v` — 2 green.
- **SC-007** (follow-up briefs on disk): `ls /home/yonk/yonk-tools/chunkshop-cs5/skill-output/mission-brief/Mission-Brief-cs5-*.md` — confirm 3 files: `cs5-provenance.md`, `cs5-cross-backend.md`, `cs5-rust-parity.md`.
- **SC-008** (CHANGELOG entry): `grep -A2 'provenance' CHANGELOG.md | head` — confirm bullet present.

Run full suite to catch any unintended regression:
```bash
cd /home/yonk/yonk-tools/chunkshop-cs5/python && uv run --no-sync pytest tests/chunkshop/ --timeout=60 -q 2>&1 | tail -20
```
Expected: all green (or skipped if PG/MariaDB unreachable for specific tests).

If any SC lacks evidence, work is NOT complete — fix the gap, then re-run DC-FINAL.

- [ ] **Step 3: Commit**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5
git add CHANGELOG.md
git commit -m "docs(changelog): add CS-5 provenance entry under [Unreleased]"
```

---

## Task 6: Push + open PR

**Brief criteria:** none directly (handoff)

- [ ] **Step 1: Push the branch**

```bash
cd /home/yonk/yonk-tools/chunkshop-cs5
git push -u origin feat/cs5-provenance
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base main --head feat/cs5-provenance --repo yonk-labs/chunkshop \
  --title "feat(code_edges): CS-5 provenance + provenance_metadata columns" \
  --body "$(cat <<'EOF'
## Summary
- Adds two additive PG columns to `code_edges`: `provenance text NOT NULL DEFAULT 'ast'` (3-value `CHECK`: `ast | scip | heuristic`) and `provenance_metadata jsonb NOT NULL DEFAULT '{}'`. Plus index `code_edges_provenance_idx` on `(project_id, provenance)`.
- `_emit` chokepoint hardcodes `provenance='ast'` + `provenance_metadata={}` on every emitted edge (all 5 emission paths inherit it; chokepoint discipline preserved from CS-2).
- `write_edges` INSERT persists both columns; ON CONFLICT DO UPDATE preserves both (tested with the pre-seed-then-flip pattern — passing for the wrong reason was the issue CS-2 caught and fixed).
- CS-2 invariants (`edge_type`, `edge_kind`, PRIMARY KEY) are **byte-identical**. Strictly additive — pg-raggraph consumers, `chunkshop impact-of` CLI, and pg-raggraph's `test_chunkshop_bridge.py` keep working with zero changes.
- No CLI surface in this PR — `--provenance` filter on `impact-of` is YAGNI until CS-3 produces non-AST edges to filter against.
- Heads-up for pg-raggraph: their `test_chunkshop_bridge.py` creates its own stand-in `code_edges` schema and will silently lack the two new columns. Update on your schedule — not blocking.

## Mission brief
`skill-output/mission-brief/Mission-Brief-cs5-provenance.md` (gitignored). Plan: `docs/superpowers/plans/2026-05-28-cs5-provenance.md`. Two follow-up briefs filed locally: cross-backend (bundled with CS-2's cross-backend brief — same DDL-seam refactor) and Rust parity (blocked on CS-2's Rust parity brief shipping first).

## Test plan
- [x] `cd python && uv run --no-sync pytest tests/chunkshop/test_provenance_types.py -v` — 2/2 pass (pure-Python types)
- [x] `cd python && uv run --no-sync pytest tests/chunkshop/test_code_relationships_extractor.py -v` — 18/18 pass (17 pre-existing additive + 1 new)
- [x] With PG on `:5434`: `pytest tests/chunkshop/test_code_edges_provenance.py -v` — 7/7 pass (CHECK reject + accept-all-3 + schema-shape + CS-2-regression + round-trip + jsonb-arbitrary + on-conflict-update)
- [x] Full suite green (extractor + runner + CS-2 typed tests all unaffected)
- [ ] Smoke-test live: run any factorial-int8 cell, inspect `SELECT provenance, COUNT(*) FROM code_edges GROUP BY 1` — expect `('ast', N)` with N > 0.

## Out of scope (deferred to follow-up briefs)
- Sink files — bundled with CS-2's cross-backend brief (same DDL-seam refactor).
- Rust port — depends on CS-2's Rust parity brief shipping first (creates the `rust/chunkshop/src/extractors/` scaffold).
- CLI `--provenance` filter — YAGNI until CS-3.
- pg-raggraph cross-repo patches.
- Tree-sitter-vs-regex differentiation within `'ast'`.
EOF
)"
```

- [ ] **Step 3: Verify CI is green**

After the PR opens, watch CI. Fix any failures in-place (additional commits on the branch); do not merge a red PR.

---

## Self-review (run before declaring the plan ready)

- **Spec coverage:** Every SC-001 through SC-008 has a task that produces evidence for it (mapped in DC-FINAL). ✓
- **Drift checkpoints:** DC-001 (Task 2 step 6), DC-002 (Task 3 step 6), DC-003 (Task 4 step 6), DC-FINAL (Task 5 step 2) — all four present as ⛔ hard gates. ✓
- **Out-of-scope discipline:** Every task's "Files" section lists only `code_relationships.py`, test files under `tests/chunkshop/`, and `CHANGELOG.md`. No `sinks/`, no `rust/`, no `cli.py`, no `pg-raggraph` paths. ✓
- **Type consistency:** `Provenance`, `PROVENANCES` names match across Tasks 1, 3, 4. ✓
- **CS-2 lessons applied:** All test snippets use `source_path=` (not `file_path=`). The ON-CONFLICT preservation test (Task 4) pre-seeds a *different* value (`'heuristic'` + `{'fake': 'data'}`) to actually exercise the SET clause, not the auto-preservation behavior. ✓
- **No placeholders:** All code blocks are complete; no TBD, no "add validation", no "similar to Task N". ✓
- **TDD discipline:** Every task is failing-test → minimal-impl → passing-test → commit. ✓
- **Task 4 step 2 expected-output note:** Honestly explains that 2 of 3 new tests would PASS before the patch (because DEFAULT clauses cover round-trip and hand-INSERT doesn't depend on write_edges) — only the on-conflict test genuinely fails before. That's the right shape; the round-trip + jsonb tests are passive regression guards. Documented in the step rather than glossing over. ✓
