"""Tests for ``CommentExtractsSource`` — the glob-driven source that
mines source files for comments and yields one ``Document`` per block.

Hermetic — all fixtures written to ``tmp_path``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chunkshop.config import CommentExtractsSource as Cfg
from chunkshop.sources.comment_extracts import CommentExtractsSource


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def test_python_emits_one_doc_per_block(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text(
        '"""Module docstring that is long enough to keep."""\n'
        "\n"
        "# header note one\n"
        "# header note two\n"
        "def foo():\n"
        '    """foo docstring long enough."""\n'
        "    # body note that is long\n"
        "    return 1\n"
    )
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.py"), min_chars=10)
    )
    docs = list(src.iter_documents())
    # Expect: 1 module docstring + 1 grouped line block + 1 function docstring + 1 in-body line.
    assert len(docs) == 4
    # IDs include source path + line.
    for d in docs:
        assert str(p) in d.id
        assert d.metadata["language"] == "python"
        assert "start_line" in d.metadata
        assert "end_line" in d.metadata
        assert "kind" in d.metadata


def test_python_skip_docstrings_when_disabled(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text(
        '"""drop this docstring."""\n'
        "# keep this line block\n"
        "# second line\n"
    )
    src = CommentExtractsSource(
        Cfg(
            type="comment_extracts",
            glob=str(tmp_path / "*.py"),
            include_docstrings=False,
            min_chars=10,
        )
    )
    docs = list(src.iter_documents())
    assert len(docs) == 1
    assert docs[0].metadata["kind"] == "line"


def test_python_skip_pragmas_filters_shebang_and_directives(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text(
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "import os  # noqa\n"
        "# real comment that's long enough to survive\n"
    )
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.py"), min_chars=10)
    )
    docs = list(src.iter_documents())
    assert len(docs) == 1
    assert "real comment" in docs[0].content


# ---------------------------------------------------------------------------
# Java / JS / Go
# ---------------------------------------------------------------------------


def test_java_extracts_line_and_block_comments(tmp_path: Path) -> None:
    p = tmp_path / "Foo.java"
    p.write_text(
        "// Foo handles the Foo, please be kind.\n"
        "// Author: nobody.\n"
        "public class Foo {\n"
        "    /* JavaDoc style block long enough to keep around */\n"
        "    String s = \"// not a comment in here\";\n"
        "}\n"
    )
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.java"), min_chars=10)
    )
    docs = list(src.iter_documents())
    contents = [d.content for d in docs]
    assert any("Foo handles" in c for c in contents)
    assert any("JavaDoc style" in c for c in contents)
    # The // inside the string MUST NOT have produced a Document.
    assert not any("not a comment" in c for c in contents)


def test_typescript_jsdoc_block_extracted(tmp_path: Path) -> None:
    p = tmp_path / "foo.ts"
    p.write_text(
        "/**\n"
        " * Foo: long enough to keep this comment.\n"
        " * @param x stuff\n"
        " */\n"
        "export function foo(x: number) { return x; }\n"
    )
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.ts"), min_chars=10)
    )
    docs = list(src.iter_documents())
    assert len(docs) == 1
    assert docs[0].metadata["language"] == "typescript"
    assert docs[0].metadata["kind"] == "block"
    assert "Foo: long enough" in docs[0].content


def test_go_package_doc_and_block(tmp_path: Path) -> None:
    p = tmp_path / "foo.go"
    p.write_text(
        "// Package foo provides foo. Long enough to keep.\n"
        "package foo\n"
        "\n"
        "//go:build linux\n"
        "// short keep me\n"
        "func Foo() {}\n"
    )
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.go"), min_chars=10)
    )
    docs = list(src.iter_documents())
    contents = [d.content for d in docs]
    assert any("Package foo provides foo" in c for c in contents)
    # go:build pragma must not appear.
    assert not any("go:build" in c for c in contents)


# ---------------------------------------------------------------------------
# Granularity modes
# ---------------------------------------------------------------------------


def test_per_file_granularity_concatenates(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text(
        "# first comment block.\n"
        "x = 1\n"
        "# second comment block separate.\n"
    )
    src = CommentExtractsSource(
        Cfg(
            type="comment_extracts",
            glob=str(tmp_path / "*.py"),
            granularity="per_file",
            min_chars=10,
        )
    )
    docs = list(src.iter_documents())
    assert len(docs) == 1
    assert docs[0].id == f"{p}::comments"
    assert "first comment block." in docs[0].content
    assert "second comment block separate." in docs[0].content
    assert docs[0].metadata["block_count"] == 2
    assert docs[0].metadata["first_line"] == 1
    assert docs[0].metadata["last_line"] == 3


def test_per_line_granularity_explodes_line_blocks(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text(
        "# first line of the block long\n"
        "# second line of the block long\n"
        "# third line of the block long\n"
    )
    src = CommentExtractsSource(
        Cfg(
            type="comment_extracts",
            glob=str(tmp_path / "*.py"),
            granularity="per_line",
            min_chars=10,
        )
    )
    docs = list(src.iter_documents())
    # Three separate Documents, one per line.
    assert len(docs) == 3
    assert all(d.metadata["language"] == "python" for d in docs)
    # Lines should be 1, 2, 3.
    assert sorted(d.metadata["start_line"] for d in docs) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Filtering / edge cases
# ---------------------------------------------------------------------------


def test_min_chars_filters_noise(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text(
        "# x\n"
        "# todo\n"
        "# this comment is plenty long to survive\n"
    )
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.py"), min_chars=20)
    )
    docs = list(src.iter_documents())
    # The three-line group is one block whose total text length > 20
    # so it survives — the short lines come along for free because
    # they're in the same group. min_chars is a per-block filter,
    # not per-line, by design.
    assert len(docs) == 1
    assert "plenty long" in docs[0].content


def test_languages_allowlist_skips_others(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("# python comment that is long enough\n")
    (tmp_path / "a.js").write_text("// javascript comment that is long enough\n")
    src = CommentExtractsSource(
        Cfg(
            type="comment_extracts",
            glob=str(tmp_path / "*"),
            languages=["python"],
            min_chars=10,
        )
    )
    docs = list(src.iter_documents())
    assert len(docs) == 1
    assert docs[0].metadata["language"] == "python"


def test_empty_file_yields_no_docs(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("")
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.py"), min_chars=10)
    )
    assert list(src.iter_documents()) == []


def test_file_with_no_comments_yields_no_docs(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\nprint(x + y)\n")
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.py"), min_chars=10)
    )
    assert list(src.iter_documents()) == []


def test_glob_no_match_raises(tmp_path: Path) -> None:
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.py"))
    )
    with pytest.raises(ValueError, match="no files matched glob"):
        list(src.iter_documents())


def test_glob_picks_up_multiple_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("# comment from a long enough to keep\n")
    (tmp_path / "b.py").write_text("# comment from b long enough to keep\n")
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*.py"), min_chars=10)
    )
    docs = sorted(src.iter_documents(), key=lambda d: d.id)
    assert len(docs) == 2
    contents = [d.content for d in docs]
    assert any("from a" in c for c in contents)
    assert any("from b" in c for c in contents)


def test_unknown_extension_silently_skipped(tmp_path: Path) -> None:
    (tmp_path / "a.unknown").write_text("# pseudo comment\n")
    (tmp_path / "a.py").write_text("# real python comment long enough\n")
    src = CommentExtractsSource(
        Cfg(type="comment_extracts", glob=str(tmp_path / "*"), min_chars=10)
    )
    docs = list(src.iter_documents())
    # Only the .py file's comment.
    assert len(docs) == 1
    assert docs[0].metadata["language"] == "python"


def test_load_source_factory_dispatches(tmp_path: Path) -> None:
    """``load_source`` recognises the new discriminator value."""
    from chunkshop.sources import load_source

    (tmp_path / "a.py").write_text("# real comment kept\n")
    cfg = Cfg(type="comment_extracts", glob=str(tmp_path / "*.py"), min_chars=10)
    src = load_source(cfg)
    docs = list(src.iter_documents())
    assert len(docs) == 1
