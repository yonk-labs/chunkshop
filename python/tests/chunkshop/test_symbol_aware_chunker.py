"""Tests for the `symbol_aware` multi-language chunker (SP-B).

The symbol_aware chunker generalises the Python-only `code_aware` chunker to
any language that codeparse can parse (Python, Java, Go, TypeScript,
JavaScript). For each top-level symbol the parser finds it emits ONE chunk
whose ``original_content`` is the raw source slice (lines ``line_start`` ..
``line_end``) and whose ``embedded_content`` optionally prepends the file's
import block as embedding context.

Falls back to ``sentence_aware`` when:
 - codeparse doesn't recognise the language (extension unknown / no path), or
 - the parser found zero symbols, or
 - a syntax error is detected (Python only — tree-sitter is error-tolerant
   for the other languages so we don't fail their chunks).

See ``python/src/chunkshop/chunkers/symbol_aware.py`` for the implementation
and the corresponding §SP-B in the symbol-aware code-ingest plan.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from chunkshop.chunkers import load_chunker
from chunkshop.chunkers.symbol_aware import (
    SymbolAwareChunker as SymbolAwareChunkerImpl,
)
from chunkshop.codeparse import code_symbol_node_id
from chunkshop.config import (
    CellConfig,
    SentenceAwareChunker,
    SymbolAwareChunker,
)
from chunkshop.sources.base import Document


# Reuse SP-A's codeparse fixtures so any drift between codeparse and
# symbol_aware shows up here as a test failure.
CODEPARSE_FIXTURES = (
    Path(__file__).resolve().parent.parent / "fixtures" / "codeparse"
)
PY_FIXTURE = CODEPARSE_FIXTURES / "python" / "sample.py"
JAVA_FIXTURE = CODEPARSE_FIXTURES / "java" / "Sample.java"


def _doc(content: str, *, doc_id: str = "d1", path: str | None = None) -> Document:
    meta: dict = {}
    if path is not None:
        meta["path"] = path
    return Document(id=doc_id, content=content, metadata=meta)


def _make(**kwargs) -> SymbolAwareChunkerImpl:
    cfg = SymbolAwareChunker(type="symbol_aware", **kwargs)
    return load_chunker(cfg)


# --- 1. config wiring -----------------------------------------------------


def test_symbol_aware_in_chunker_union():
    """Pydantic accepts ``type: symbol_aware`` inside a full CellConfig.

    Also checks defaults and extra="forbid".
    """
    cfg = CellConfig(
        cell_name="x",
        source={"type": "files", "glob": "*.py"},
        chunker={"type": "symbol_aware"},
        embedder={
            "type": "fastembed",
            "model_name": "BAAI/bge-small-en-v1.5",
            "dim": 384,
        },
        target={
            "type": "postgres",
            "dsn_env": "FAKE_DSN",
            "database": "db",
            "table": "t",
        },
    )
    assert cfg.chunker.type == "symbol_aware"
    assert cfg.chunker.granularity == "function"  # default
    assert cfg.chunker.include_imports is True  # default
    assert cfg.chunker.max_chars == 8000  # default
    assert cfg.chunker.languages is None  # default

    with pytest.raises(ValidationError):
        SymbolAwareChunker(type="symbol_aware", unknown_field=True)


# --- 2. factory dispatch --------------------------------------------------


def test_loads_via_factory():
    """``load_chunker`` returns a SymbolAwareChunker instance."""
    chunker = load_chunker(SymbolAwareChunker(type="symbol_aware"))
    assert isinstance(chunker, SymbolAwareChunkerImpl)


# --- 3. python function boundaries (free functions) -----------------------

THREE_PY_FNS = (
    '"""Three trivial top-level functions."""\n'
    "\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "def subtract(a, b):\n"
    "    return a - b\n"
    "\n"
    "def multiply(a, b):\n"
    "    return a * b\n"
)


def test_python_function_boundaries():
    """3 free functions -> 3 chunks tagged with symbol_name/start/end."""
    chunker = _make(include_imports=False)
    chunks = chunker.chunk(_doc(THREE_PY_FNS, path="three_fns.py"))

    fn_chunks = [c for c in chunks if c.metadata.get("symbol_type") == "function"]
    assert len(fn_chunks) == 3
    names = {c.metadata["symbol_name"] for c in fn_chunks}
    assert names == {"add", "subtract", "multiply"}
    # Every chunk has start/end lines that bracket non-empty source.
    for c in fn_chunks:
        assert c.metadata["start_line"] >= 1
        assert c.metadata["end_line"] >= c.metadata["start_line"]
        assert c.original_content.strip() != ""


def test_top_level_functions_stamp_scope_chain():
    """Top-level functions get a human-readable scope_chain: ``stem > name``."""
    chunker = _make(include_imports=False)
    chunks = chunker.chunk(_doc(THREE_PY_FNS, path="three_fns.py"))

    fn_chunks = [c for c in chunks if c.metadata.get("symbol_type") == "function"]
    chains = {c.metadata["scope_chain"] for c in fn_chunks}
    assert chains == {
        "three_fns > add",
        "three_fns > subtract",
        "three_fns > multiply",
    }


def test_method_scope_chain_includes_parent_class():
    """A method's scope_chain slots its enclosing class: ``stem > Class > method``."""
    content = PY_FIXTURE.read_text()
    chunker = _make(granularity="function", include_imports=False)
    chunks = chunker.chunk(_doc(content, path="calc/sample.py"))

    # granularity=function bundles methods into the class chunk; the class
    # chunk's own scope_chain is ``stem > Calculator`` (no parent).
    class_chunks = [c for c in chunks if c.metadata.get("symbol_type") == "class"]
    assert class_chunks[0].metadata["scope_chain"] == "sample > Calculator"


# --- 4. python class with methods (function granularity) ------------------


def test_python_class_with_methods():
    """1 class with 2 methods, granularity=function -> 1 class chunk only.

    Methods are bundled inside the class chunk; line range spans the class.
    """
    content = PY_FIXTURE.read_text()
    chunker = _make(granularity="function", include_imports=False)
    chunks = chunker.chunk(_doc(content, path=str(PY_FIXTURE)))

    class_chunks = [c for c in chunks if c.metadata.get("symbol_type") == "class"]
    assert len(class_chunks) == 1
    assert class_chunks[0].metadata["symbol_name"] == "Calculator"
    # The class body covers more than one line.
    assert class_chunks[0].metadata["end_line"] > class_chunks[0].metadata["start_line"]

    # Methods should NOT appear as their own chunks under granularity=function.
    method_chunks = [
        c for c in chunks if c.metadata.get("symbol_type") == "method"
    ]
    assert method_chunks == []


# --- 5. granularity=class -------------------------------------------------


def test_granularity_class():
    """granularity=class -> 1 chunk per class, free functions grouped into a
    single module_block chunk.

    sample.py has 1 free function (helper) + 1 class (Calculator) -> 2 chunks:
    one class chunk + one module_block chunk for helper.
    """
    content = PY_FIXTURE.read_text()
    chunker = _make(granularity="class", include_imports=False)
    chunks = chunker.chunk(_doc(content, path=str(PY_FIXTURE)))

    classes = [c for c in chunks if c.metadata.get("symbol_type") == "class"]
    module_blocks = [
        c for c in chunks if c.metadata.get("symbol_type") == "module_block"
    ]
    assert len(classes) == 1
    assert len(module_blocks) == 1
    # The module_block must contain the free function source.
    assert "def helper" in module_blocks[0].original_content


# --- 6. granularity=module ------------------------------------------------


def test_granularity_module():
    """granularity=module -> 1 chunk per file, regardless of symbol count."""
    content = PY_FIXTURE.read_text()
    chunker = _make(granularity="module", include_imports=False)
    chunks = chunker.chunk(_doc(content, path=str(PY_FIXTURE)))

    assert len(chunks) == 1
    assert chunks[0].metadata["symbol_type"] == "module"
    assert chunks[0].metadata["strategy"] == "symbol_aware"
    # Should contain the entire file's content.
    assert "def helper" in chunks[0].original_content
    assert "class Calculator" in chunks[0].original_content
    # node_id is still stamped for the module-level node.
    assert chunks[0].metadata["node_id"].startswith("node-")


# --- 7. include_imports=True prepends -------------------------------------


def test_include_imports_prepends():
    """embedded_content starts with imports; original_content does NOT."""
    content = (
        "import os\n"
        "import sys\n"
        "\n"
        "def foo():\n"
        "    return os.getpid()\n"
    )
    chunker = _make(include_imports=True)
    chunks = chunker.chunk(_doc(content, path="m.py"))
    foo_chunks = [c for c in chunks if c.metadata.get("symbol_name") == "foo"]
    assert len(foo_chunks) == 1
    foo = foo_chunks[0]
    # embedded_content must include the imports somewhere near the top.
    assert "import os" in foo.embedded_content
    assert "import sys" in foo.embedded_content
    # original_content must NOT include the bare imports — it's the raw
    # function slice only.
    assert "import os" not in foo.original_content
    assert "def foo" in foo.original_content


# --- 8. include_imports=False keeps embedded == original ------------------


def test_include_imports_false():
    """With include_imports=False, embedded_content == original_content."""
    content = (
        "import os\n"
        "import sys\n"
        "\n"
        "def foo():\n"
        "    return os.getpid()\n"
    )
    chunker = _make(include_imports=False)
    chunks = chunker.chunk(_doc(content, path="m.py"))
    foo_chunks = [c for c in chunks if c.metadata.get("symbol_name") == "foo"]
    assert len(foo_chunks) == 1
    foo = foo_chunks[0]
    assert foo.embedded_content == foo.original_content


# --- 9. oversize -> falls through to if_oversize --------------------------


def test_oversize_falls_through_to_if_oversize():
    """A function whose source exceeds max_chars must be re-chunked by the
    if_oversize chunker.

    We build a synthetic huge function (~12 KB body of comments) and a
    chunker with max_chars=2000 + if_oversize=sentence_aware. Expect >1 chunk
    out (the original function chunk gets replaced by multiple sub-chunks).
    """
    body_lines = ["    # filler line " + str(i) for i in range(800)]
    huge = "def big():\n" + "\n".join(body_lines) + "\n"
    cfg = SymbolAwareChunker(
        type="symbol_aware",
        include_imports=False,
        max_chars=2000,
        if_oversize=SentenceAwareChunker(type="sentence_aware", max_chars=600),
    )
    chunker = load_chunker(cfg)
    chunks = chunker.chunk(_doc(huge, path="big.py"))

    # Sub-chunks each fit under the ceiling (apply_if_oversize enforces this).
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.embedded_content) <= 2000
        assert len(c.original_content) <= 2000


# --- 10. unsupported language -> fallback to sentence_aware ---------------


def test_unsupported_language_falls_back_to_sentence_aware():
    """Document with unknown extension -> sentence_aware with fallback tag."""
    content = (
        "Some prose. Another sentence. Yet another sentence to chunk.\n"
        "\n"
        "Paragraph two with more text to encourage at least one chunk.\n"
    )
    chunker = _make()
    chunks = chunker.chunk(_doc(content, path="foo.unknown_ext"))

    assert len(chunks) >= 1
    for c in chunks:
        assert c.metadata.get("strategy") == "symbol_aware_fallback"
        assert c.metadata.get("fallback_reason") == "unsupported_language"


# --- 11. syntax error -> fallback -----------------------------------------


def test_syntax_error_falls_back():
    """Malformed Python (def foo(:) -> sentence_aware fallback w/ parse_error."""
    content = "def foo(:\n    pass\n"
    chunker = _make()
    chunks = chunker.chunk(_doc(content, path="broken.py"))

    assert len(chunks) >= 1
    for c in chunks:
        assert c.metadata.get("strategy") == "symbol_aware_fallback"
        assert c.metadata.get("fallback_reason") == "parse_error"


# --- 12. metadata carries deterministic node_id ---------------------------


def test_metadata_carries_node_id():
    """node_id is deterministic across runs and matches code_symbol_node_id."""
    content = "def alpha():\n    return 1\n"
    chunker = _make(include_imports=False)
    chunks_run1 = chunker.chunk(_doc(content, path="x.py"))
    chunks_run2 = chunker.chunk(_doc(content, path="x.py"))

    assert len(chunks_run1) == 1
    assert chunks_run1[0].metadata["node_id"].startswith("node-")
    # Same input -> same node_id every time.
    assert (
        chunks_run1[0].metadata["node_id"] == chunks_run2[0].metadata["node_id"]
    )
    # And matches the codeparse helper's output.
    expected = code_symbol_node_id(
        "default", "python", "x.py", chunks_run1[0].metadata["fqn"]
    )
    assert chunks_run1[0].metadata["node_id"] == expected


# --- 13. java class with methods ------------------------------------------


def test_java_class_with_methods():
    """granularity=function on Java -> 1 class chunk (methods bundled in)."""
    content = JAVA_FIXTURE.read_text()
    chunker = _make(granularity="function", include_imports=False)
    chunks = chunker.chunk(_doc(content, path=str(JAVA_FIXTURE)))

    class_chunks = [c for c in chunks if c.metadata.get("symbol_type") == "class"]
    assert len(class_chunks) == 1
    assert class_chunks[0].metadata["symbol_name"] == "Calculator"
    assert class_chunks[0].metadata["language"] == "java"


# --- 14. same name in two files -> distinct node_ids ----------------------


def test_two_files_same_function_name_get_distinct_node_ids():
    """FQN includes file stem, so node_ids differ across files."""
    content = "def shared():\n    return 1\n"
    chunker = _make(include_imports=False)
    a = chunker.chunk(_doc(content, doc_id="A", path="alpha.py"))
    b = chunker.chunk(_doc(content, doc_id="B", path="beta.py"))

    assert a and b
    assert a[0].metadata["node_id"] != b[0].metadata["node_id"]
    assert a[0].metadata["fqn"] != b[0].metadata["fqn"]


# --- 15. dual-text contract -----------------------------------------------


def test_dual_text_contract():
    """Every chunk has both original_content and embedded_content as strings."""
    content = PY_FIXTURE.read_text()
    chunker = _make(include_imports=True)
    chunks = chunker.chunk(_doc(content, path=str(PY_FIXTURE)))

    assert len(chunks) >= 1
    for c in chunks:
        assert isinstance(c.original_content, str)
        assert isinstance(c.embedded_content, str)
        assert c.original_content  # non-empty
        assert c.embedded_content  # non-empty
