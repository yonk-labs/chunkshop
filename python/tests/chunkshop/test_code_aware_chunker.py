"""Tests for the `code_aware` AST chunker.

The chunker splits Python source code at top-level function/class boundaries
using the stdlib `ast` module. Non-Python sources fall back to the configured
`if_oversize` chunker (default: `sentence_aware`). Malformed Python (SyntaxError
on parse) yields a single fallback chunk with `strategy='code_aware_fallback'`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from chunkshop.chunkers import load_chunker
from chunkshop.chunkers.code_aware import CodeAwareChunker as CodeAwareChunkerImpl
from chunkshop.config import (
    CodeAwareChunker,
    SentenceAwareChunker,
    CellConfig,
)
from chunkshop.sources.base import Document


FIXTURES = Path(__file__).parent.parent / "fixtures" / "code_aware"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _doc(content: str, *, doc_id: str = "d1", path: str | None = None) -> Document:
    meta: dict = {}
    if path is not None:
        meta["path"] = path
    return Document(id=doc_id, content=content, metadata=meta)


# --- T1: config wiring -----------------------------------------------------


def test_code_aware_in_chunker_config_union():
    """Pydantic accepts `type: code_aware` inside a full CellConfig."""
    cfg = CellConfig(
        cell_name="x",
        source={"type": "files", "glob": "*.py"},
        chunker={"type": "code_aware"},
        embedder={"type": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dim": 384},
        target={
            "type": "postgres",
            "dsn_env": "FAKE_DSN",
            "database": "db",
            "table": "t",
        },
    )
    assert cfg.chunker.type == "code_aware"
    assert cfg.chunker.max_chars == 4000  # default
    assert cfg.chunker.include_imports is True  # default
    assert cfg.chunker.language == "auto"  # default

    # extra="forbid" still applies — typos rejected
    with pytest.raises(ValidationError):
        CodeAwareChunker(type="code_aware", unknown_field=True)


def test_loads_via_factory():
    """load_chunker({type: 'code_aware'}) returns a CodeAwareChunker instance."""
    chunker = load_chunker(CodeAwareChunker(type="code_aware"))
    assert isinstance(chunker, CodeAwareChunkerImpl)


# --- T2: AST splitting behavior -------------------------------------------


def test_splits_at_function_boundaries():
    """Three top-level functions -> three chunks."""
    content = _read_fixture("simple_three_functions.py")
    chunker = load_chunker(CodeAwareChunker(type="code_aware", include_imports=False))
    chunks = chunker.chunk(_doc(content, path="simple_three_functions.py"))

    func_chunks = [c for c in chunks if c.metadata.get("node_type") == "function"]
    assert len(func_chunks) == 3
    names = {c.metadata["node_name"] for c in func_chunks}
    assert names == {"add", "subtract", "multiply"}


def test_splits_at_class_boundaries():
    """One class with 2 methods -> exactly one class chunk (class is the boundary)."""
    content = _read_fixture("class_with_methods.py")
    chunker = load_chunker(CodeAwareChunker(type="code_aware", include_imports=False))
    chunks = chunker.chunk(_doc(content, path="class_with_methods.py"))

    class_chunks = [c for c in chunks if c.metadata.get("node_type") == "class"]
    assert len(class_chunks) == 1
    assert class_chunks[0].metadata["node_name"] == "Calculator"

    # No method-level chunks emitted — methods stay inside the class chunk
    fn_chunks = [c for c in chunks if c.metadata.get("node_type") == "function"]
    assert fn_chunks == []
    # Sanity: the class body actually contains both methods
    assert "def add" in class_chunks[0].original_content
    assert "def subtract" in class_chunks[0].original_content


def test_preserves_full_function_body():
    """Function chunk includes the docstring AND the body."""
    content = _read_fixture("simple_three_functions.py")
    chunker = load_chunker(CodeAwareChunker(type="code_aware", include_imports=False))
    chunks = chunker.chunk(_doc(content, path="simple_three_functions.py"))

    add_chunk = next(
        c for c in chunks
        if c.metadata.get("node_name") == "add"
        and c.metadata.get("node_type") == "function"
    )
    assert '"""Return the sum of a and b."""' in add_chunk.original_content
    assert "return a + b" in add_chunk.original_content


# --- T3: imports prepended for context ------------------------------------


def test_imports_prepended_when_configured():
    """include_imports=True -> chunk.embedded_content starts with the import block."""
    content = _read_fixture("with_imports_and_constants.py")
    chunker = load_chunker(CodeAwareChunker(type="code_aware", include_imports=True))
    chunks = chunker.chunk(_doc(content, path="with_imports_and_constants.py"))

    fn_chunks = [c for c in chunks if c.metadata.get("node_type") == "function"]
    assert fn_chunks, "expected at least one function chunk"
    for c in fn_chunks:
        # embedded_content begins with the import lines (top of the file)
        assert "import os" in c.embedded_content
        assert "import sys" in c.embedded_content
        assert "from pathlib import Path" in c.embedded_content
        # And the original_content does NOT include the imports — only the function body
        assert "import os" not in c.original_content


def test_imports_skipped_when_disabled():
    """include_imports=False -> embedded_content == original_content."""
    content = _read_fixture("with_imports_and_constants.py")
    chunker = load_chunker(CodeAwareChunker(type="code_aware", include_imports=False))
    chunks = chunker.chunk(_doc(content, path="with_imports_and_constants.py"))

    fn_chunks = [c for c in chunks if c.metadata.get("node_type") == "function"]
    assert fn_chunks
    for c in fn_chunks:
        assert c.embedded_content == c.original_content


# --- T4: metadata content -------------------------------------------------


def test_metadata_carries_node_name_and_lines():
    """Every chunk has node_name, start_line, end_line, node_type."""
    content = _read_fixture("simple_three_functions.py")
    chunker = load_chunker(CodeAwareChunker(type="code_aware"))
    chunks = chunker.chunk(_doc(content, path="simple_three_functions.py"))

    for c in chunks:
        assert "node_name" in c.metadata
        assert "node_type" in c.metadata
        assert "start_line" in c.metadata
        assert "end_line" in c.metadata
        assert c.metadata["strategy"] == "code_aware"
        # start_line <= end_line, both positive
        assert 1 <= c.metadata["start_line"] <= c.metadata["end_line"]


# --- T5: oversize handling ------------------------------------------------


def test_oversize_function_kept_whole_by_default():
    """A 5000+ char function with max_chars=4000 and no if_oversize: stays whole."""
    content = _read_fixture("oversized_function.py")
    chunker = load_chunker(
        CodeAwareChunker(type="code_aware", max_chars=4000, include_imports=False)
    )
    chunks = chunker.chunk(_doc(content, path="oversized_function.py"))

    # Exactly one function chunk — oversize is allowed when no fallback configured
    fn_chunks = [c for c in chunks if c.metadata.get("node_type") == "function"]
    assert len(fn_chunks) == 1
    assert fn_chunks[0].metadata["node_name"] == "big_function"
    assert len(fn_chunks[0].original_content) > 4000


def test_oversize_function_split_via_if_oversize():
    """With if_oversize=sentence_aware, oversize functions fall through to sub-chunks."""
    content = _read_fixture("oversized_function.py")
    chunker = load_chunker(
        CodeAwareChunker(
            type="code_aware",
            max_chars=2000,
            include_imports=False,
            if_oversize=SentenceAwareChunker(
                type="sentence_aware", max_chars=1500, min_chars=50
            ),
        )
    )
    chunks = chunker.chunk(_doc(content, path="oversized_function.py"))

    # The oversize big_function is split into multiple sub-chunks
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.embedded_content) <= 2000
        assert len(c.original_content) <= 2000


# --- T6: non-Python fallback ----------------------------------------------


def test_non_python_falls_back_to_sentence_aware():
    """Non-.py doc -> emitted via SentenceAwareChunker, not the AST path."""
    js_content = (
        "function hello() { return 'world'; }\n\n"
        "function add(a, b) { return a + b; }\n\n"
        "const X = 1;\n"
    )
    chunker = load_chunker(CodeAwareChunker(type="code_aware"))
    chunks = chunker.chunk(_doc(js_content, path="foo.js"))

    assert chunks  # got at least one chunk
    for c in chunks:
        # Fallback chunker is sentence_aware — none of these should be AST chunks
        assert c.metadata.get("strategy") != "code_aware"
        assert c.metadata.get("strategy") != "code_aware_fallback"
        # node_name / node_type are AST-only — fallback path skips them
        assert "node_name" not in c.metadata


# --- T7: malformed Python -------------------------------------------------


def test_syntax_error_fallback_emits_whole():
    """Malformed Python: emit single chunk with strategy='code_aware_fallback'."""
    bad = "def foo(:\n    return 1\n"
    chunker = load_chunker(CodeAwareChunker(type="code_aware"))
    chunks = chunker.chunk(_doc(bad, path="malformed.py"))

    assert len(chunks) == 1
    assert chunks[0].metadata["strategy"] == "code_aware_fallback"
    assert chunks[0].original_content == bad


# --- T8: module-level statements -----------------------------------------


def test_module_level_assignments_grouped():
    """Top-level imports + constants form a single 'module_block' chunk."""
    content = _read_fixture("with_imports_and_constants.py")
    chunker = load_chunker(CodeAwareChunker(type="code_aware", include_imports=False))
    chunks = chunker.chunk(_doc(content, path="with_imports_and_constants.py"))

    # First chunk should be the module-level block (imports + constants)
    module_chunks = [c for c in chunks if c.metadata.get("node_type") == "module_block"]
    assert len(module_chunks) == 1
    mb = module_chunks[0]
    # Contains imports and module constants
    assert "import os" in mb.original_content
    assert "DEFAULT_TIMEOUT" in mb.original_content
    assert "MAX_RETRIES" in mb.original_content

    # Function chunks come after (higher seq_num) the module_block
    fn_chunks = [c for c in chunks if c.metadata.get("node_type") == "function"]
    assert fn_chunks
    assert all(fc.seq_num > mb.seq_num for fc in fn_chunks)


# --- T9: empty file -------------------------------------------------------


def test_empty_file_yields_no_chunks():
    """Empty content -> empty chunk list."""
    chunker = load_chunker(CodeAwareChunker(type="code_aware"))
    chunks = chunker.chunk(_doc("", path="empty.py"))
    assert chunks == []
