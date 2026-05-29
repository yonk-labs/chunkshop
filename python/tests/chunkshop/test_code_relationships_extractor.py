"""Tests for the ``code_relationships`` extractor (SP-C).

Covers:
  - Pydantic union membership + factory dispatch
  - Phase-1 (per-chunk) callee metadata shape
  - Phase-2 (corpus-level) unique-name resolution with confidence bands
  - Ambiguous-name resolution emitting low-confidence edges
  - Cross-file inheritance / implementation edges (Python + Java)
  - DB-backed schema + write helpers (skip if Postgres unreachable)
  - Dual-text contract preservation (extractor mutates neither
    ``original_content`` nor ``embedded_content`` since the runner already
    treats them as immutable inputs)
"""
from __future__ import annotations

import importlib.util
import os
import uuid

import pytest


# ---------------------------------------------------------------------------
# 1. Config union + factory dispatch
# ---------------------------------------------------------------------------


def test_in_extractor_union() -> None:
    """The discriminated union accepts ``type=code_relationships``."""
    from pydantic import TypeAdapter

    from chunkshop.config import ExtractorConfig

    adapter = TypeAdapter(ExtractorConfig)
    cfg = adapter.validate_python({"type": "code_relationships"})
    # Round-trip should preserve defaults.
    assert cfg.type == "code_relationships"
    assert cfg.target_schema is None
    assert 0.0 < cfg.unique_match_confidence <= 1.0
    assert 0.0 < cfg.ambiguous_match_confidence < cfg.unique_match_confidence


def test_loads_via_factory() -> None:
    """``load_extractor`` returns a ``CodeRelationshipsExtractor`` instance."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor
    from chunkshop.extractors.code_relationships import CodeRelationshipsExtractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    assert isinstance(extractor, CodeRelationshipsExtractor)


# ---------------------------------------------------------------------------
# 2. Per-chunk callee metadata
# ---------------------------------------------------------------------------


_PY_TEXT_A = """\
def helper(x):
    return x + 1


def call_helper(y):
    z = helper(y)
    return z
"""


def test_per_chunk_callees_for_python_function() -> None:
    """``extract`` populates ``metadata['callees']`` for a Python snippet."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    result = extractor.extract(_PY_TEXT_A)

    assert result.tags == []  # this extractor never emits tags
    callees = result.metadata.get("callees")
    assert isinstance(callees, list)
    assert callees, "expected at least one callee from call_helper -> helper"
    sample = callees[0]
    assert {"name", "line", "snippet", "resolved_intra_file"}.issubset(sample.keys())


def test_intra_file_call_marked_resolved() -> None:
    """``call_helper`` calls sibling ``helper`` -> ``resolved_intra_file=True``."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    result = extractor.extract(_PY_TEXT_A)
    helpers = [c for c in result.metadata["callees"] if c["name"] == "helper"]
    assert helpers, "extractor should have spotted call_helper -> helper"
    assert any(c["resolved_intra_file"] for c in helpers)


# ---------------------------------------------------------------------------
# 3. Cross-file resolution
# ---------------------------------------------------------------------------


_PY_DEFINES_HELPER = "def helper(value):\n    return value * 2\n"
_PY_DEFINES_HELPER_TWIN = (
    "def helper(value):\n    # different definition, same bare name\n    return value\n"
)
_PY_CALLS_HELPER = "def caller(v):\n    return helper(v)\n"


def test_cross_file_unique_name_resolves_with_high_confidence() -> None:
    """Single ``helper`` defined corpus-wide -> one high-confidence edge."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    # Feed each "file" through extract; the extractor accumulates state.
    extractor.extract(_PY_DEFINES_HELPER, source_path="a.py", language="python")
    extractor.extract(_PY_CALLS_HELPER, source_path="b.py", language="python")
    edges = extractor.finalize()

    calls = [e for e in edges if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")]
    assert len(calls) == 1
    assert calls[0]["confidence"] >= 0.85


def test_cross_file_ambiguous_name_emits_low_confidence() -> None:
    """Two ``helper`` defs corpus-wide -> two ambiguous edges from one call."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    cfg = Cfg(type="code_relationships")
    extractor = load_extractor(cfg)
    extractor.extract(_PY_DEFINES_HELPER, source_path="a.py", language="python")
    extractor.extract(_PY_DEFINES_HELPER_TWIN, source_path="c.py", language="python")
    extractor.extract(_PY_CALLS_HELPER, source_path="b.py", language="python")
    edges = extractor.finalize()

    calls = [e for e in edges if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")]
    assert len(calls) == 2
    for e in calls:
        assert e["confidence"] == pytest.approx(cfg.ambiguous_match_confidence)


_PY_CALLS_EXTERNAL = "import requests\n\ndef fetch(url):\n    return requests.get(url)\n"


def test_external_call_not_emitted() -> None:
    """Calls to symbols absent from the corpus produce no edges."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    extractor.extract(_PY_CALLS_EXTERNAL, source_path="ext.py", language="python")
    edges = extractor.finalize()
    calls = [e for e in edges if e["edge_type"] == "CALLS"]
    # No edge should reference a callee we never saw defined.
    assert all(e["dst_fqn"] for e in calls)  # if any edges, they must have a dst
    # In practice this corpus defines only ``fetch``; ``get`` and ``requests``
    # aren't defined, so no CALLS edge should be emitted.
    assert calls == []


# ---------------------------------------------------------------------------
# 4. INHERITS / IMPLEMENTS edges
# ---------------------------------------------------------------------------


_PY_INHERITS = """\
class A:
    pass


class B(A):
    pass
"""


def test_inherits_edge_for_python_class() -> None:
    """Same-file ``class B(A)`` produces an INHERITS edge B -> A."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    extractor.extract(_PY_INHERITS, source_path="inh.py", language="python")
    edges = extractor.finalize()
    inherits = [e for e in edges if e["edge_type"] == "INHERITS"]
    assert len(inherits) == 1
    e = inherits[0]
    assert e["src_fqn"].endswith(".B")
    assert e["dst_fqn"].endswith(".A")
    assert e["confidence"] >= 0.85


_JAVA_BASE = """\
package demo;

public class Base {
    public int value() { return 1; }
}
"""

_JAVA_DERIVED = """\
package demo;

public class Derived extends Base {
    public int value() { return 2; }
}
"""


def test_inherits_edge_cross_file_java() -> None:
    """Java ``class Derived extends Base`` across two files resolves via unique-name."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    extractor.extract(_JAVA_BASE, source_path="Base.java", language="java")
    extractor.extract(_JAVA_DERIVED, source_path="Derived.java", language="java")
    edges = extractor.finalize()
    inherits = [e for e in edges if e["edge_type"] == "INHERITS"]
    assert len(inherits) == 1
    e = inherits[0]
    assert e["src_fqn"].endswith(".Derived")
    assert e["dst_fqn"].endswith(".Base")
    assert e["confidence"] >= 0.85


_JAVA_IFACE = """\
package demo;

public interface Greeter {
    String hello();
}
"""

_JAVA_IMPLEMENTS = """\
package demo;

public class Hello implements Greeter {
    public String hello() { return "hi"; }
}
"""


def test_implements_edge_for_java_class() -> None:
    """``class Hello implements Greeter`` produces an IMPLEMENTS edge."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    extractor.extract(_JAVA_IFACE, source_path="Greeter.java", language="java")
    extractor.extract(_JAVA_IMPLEMENTS, source_path="Hello.java", language="java")
    edges = extractor.finalize()
    impls = [e for e in edges if e["edge_type"] == "IMPLEMENTS"]
    assert len(impls) == 1
    e = impls[0]
    assert e["src_fqn"].endswith(".Hello")
    assert e["dst_fqn"].endswith(".Greeter")


# ---------------------------------------------------------------------------
# 5. DB-backed schema + write helpers
# ---------------------------------------------------------------------------


_DSN = os.environ.get(
    "CHUNKSHOP_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5434/chunkshop_test",
)


def _pg_up() -> bool:
    if importlib.util.find_spec("psycopg") is None:
        return False
    try:
        import psycopg

        with psycopg.connect(_DSN, connect_timeout=2):
            return True
    except Exception:
        return False


_PG_REASON = "pg test DB unreachable"


@pytest.mark.skipif(not _pg_up(), reason=_PG_REASON)
def test_write_edges_schema_creates_table() -> None:
    """``write_edges_schema`` creates the table and its three indexes."""
    import psycopg

    from chunkshop.extractors.code_relationships import write_edges_schema

    schema = f"chunkshop_test_spc_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            conn.commit()

        write_edges_schema(_DSN, schema=schema)

        with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = 'code_edges'",
                (schema,),
            )
            assert cur.fetchone() is not None
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = %s "
                "AND tablename = 'code_edges'",
                (schema,),
            )
            idx_names = {row[0] for row in cur.fetchall()}
            # PK plus three explicit secondary indexes.
            assert any("src_idx" in n for n in idx_names)
            assert any("dst_idx" in n for n in idx_names)
            assert any("confident" in n for n in idx_names)
    finally:
        with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()


@pytest.mark.skipif(not _pg_up(), reason=_PG_REASON)
def test_write_edges_inserts_resolved_edges() -> None:
    """End-to-end: ingest -> finalize -> write_edges -> rows in code_edges."""
    import psycopg

    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor
    from chunkshop.extractors.code_relationships import write_edges, write_edges_schema

    schema = f"chunkshop_test_spc_{uuid.uuid4().hex[:8]}"
    project_id = "spc-test"
    try:
        with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            conn.commit()
        write_edges_schema(_DSN, schema=schema)

        extractor = load_extractor(Cfg(type="code_relationships"))
        extractor.extract(_PY_DEFINES_HELPER, source_path="a.py", language="python")
        extractor.extract(_PY_CALLS_HELPER, source_path="b.py", language="python")
        extractor.extract(_PY_INHERITS, source_path="inh.py", language="python")

        inserted = write_edges(
            extractor, dsn=_DSN, schema=schema, project_id=project_id
        )
        assert inserted >= 2  # at least one CALLS + one INHERITS

        with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT edge_type, src_fqn, dst_fqn, confidence FROM "{schema}".code_edges '
                "WHERE project_id = %s",
                (project_id,),
            )
            rows = cur.fetchall()
            edge_types = {r[0] for r in rows}
            assert "CALLS" in edge_types
            assert "INHERITS" in edge_types
            calls = [r for r in rows if r[0] == "CALLS"]
            assert any(r[3] >= 0.85 for r in calls)
    finally:
        with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()


# ---------------------------------------------------------------------------
# 6. Misc contracts
# ---------------------------------------------------------------------------


def test_dual_text_contract_preserved() -> None:
    """The extractor must not mutate its input string."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    text_before = _PY_TEXT_A
    snapshot = str(text_before)
    extractor.extract(text_before, source_path="x.py", language="python")
    # Python strings are immutable so a true mutation isn't even possible — this
    # test guards against the extractor returning the input text from
    # ``ExtractResult.metadata`` under either dual-text key.
    assert text_before == snapshot
    result = extractor.extract(text_before, source_path="x.py", language="python")
    assert "original_content" not in result.metadata
    assert "embedded_content" not in result.metadata


def test_parse_text_helper_works() -> None:
    """``parse_text`` round-trips like ``parse_file`` for the same source."""
    from chunkshop.codeparse import parse_file
    from chunkshop.codeparse.tree_sitter_wrapper import parse_text

    text = _PY_TEXT_A
    result = parse_text(text, language="python", file_path="a.py")
    # Identifies both top-level functions.
    names = {s.name for s in result.symbols}
    assert {"helper", "call_helper"} <= names
    # And it should agree with parse_file on the same content.
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(text)
        f.flush()
        from_file = parse_file(f.name, language="python")
    file_names = {s.name for s in from_file.symbols}
    assert names == file_names


def test_finalize_idempotent() -> None:
    """Calling ``finalize`` twice yields the same edge set."""
    from chunkshop.config import CodeRelationshipsExtractor as Cfg
    from chunkshop.extractors import load_extractor

    extractor = load_extractor(Cfg(type="code_relationships"))
    extractor.extract(_PY_DEFINES_HELPER, source_path="a.py", language="python")
    extractor.extract(_PY_CALLS_HELPER, source_path="b.py", language="python")
    extractor.extract(_PY_INHERITS, source_path="inh.py", language="python")
    first = extractor.finalize()
    second = extractor.finalize()
    assert first == second


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
        source_path="Parent.java",
    )
    ext.extract(
        "public interface Iface {}\n",
        language="java",
        source_path="Iface.java",
    )
    ext.extract(
        "public class Child extends Parent implements Iface {}\n",
        language="java",
        source_path="Child.java",
    )
    edges = ext.finalize(project_id="test")

    by_kind = {e["edge_kind"]: e for e in edges}
    assert "extends" in by_kind
    assert by_kind["extends"]["edge_type"] == "INHERITS"
    assert "implements" in by_kind
    assert by_kind["implements"]["edge_type"] == "IMPLEMENTS"


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
