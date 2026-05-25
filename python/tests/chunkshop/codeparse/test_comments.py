"""Unit tests for ``chunkshop.codeparse.comments.extract_comments``.

Hermetic — all fixtures written to ``tmp_path``. No I/O beyond pytest's
temp dirs.
"""
from __future__ import annotations

from pathlib import Path

from chunkshop.codeparse.comments import (
    CommentBlock,
    detect_language,
    extract_comments,
)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def test_python_line_comments_grouped(tmp_path: Path) -> None:
    """Consecutive ``#`` lines collapse to one CommentBlock."""
    src = (
        "x = 1\n"
        "# first line\n"
        "# second line\n"
        "# third line\n"
        "y = 2\n"
        "# isolated\n"
    )
    p = tmp_path / "a.py"
    p.write_text(src)
    blocks = extract_comments(path=p)

    # One grouped block (lines 2-4) and one isolated (line 6).
    line_blocks = [b for b in blocks if b.kind == "line"]
    assert len(line_blocks) == 2
    grouped, isolated = line_blocks
    assert grouped.start_line == 2
    assert grouped.end_line == 4
    assert grouped.text == "first line\nsecond line\nthird line"
    assert isolated.start_line == 6
    assert isolated.text == "isolated"


def test_python_docstrings_module_class_method(tmp_path: Path) -> None:
    """ast.get_docstring tags module/class/method docstrings, with ``symbol`` set."""
    src = (
        '"""Module docstring."""\n'
        "\n"
        "class Foo:\n"
        '    """Foo docstring."""\n'
        "    def bar(self):\n"
        '        """bar docstring."""\n'
        "        return 1\n"
    )
    p = tmp_path / "a.py"
    p.write_text(src)
    blocks = extract_comments(path=p)

    docs = {(b.symbol, b.text) for b in blocks if b.kind == "docstring"}
    assert (None, "Module docstring.") in docs
    assert ("Foo", "Foo docstring.") in docs
    assert ("Foo.bar", "bar docstring.") in docs


def test_python_skip_pragmas_drops_shebang_encoding_and_noqa(tmp_path: Path) -> None:
    """``# noqa``, encoding decl, and shebang are tooling chatter — skipped."""
    src = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "import os  # noqa\n"
        "from typing import Any  # type: ignore[import]\n"
        "# real comment here\n"
        "x = 1\n"
    )
    p = tmp_path / "a.py"
    p.write_text(src)
    blocks = extract_comments(path=p, skip_pragmas=True)
    texts = [b.text for b in blocks]
    assert texts == ["real comment here"]


def test_python_skip_pragmas_off_keeps_them(tmp_path: Path) -> None:
    src = "# noqa\n# real\n"
    p = tmp_path / "a.py"
    p.write_text(src)
    blocks = extract_comments(path=p, skip_pragmas=False)
    # Both kept; they're contiguous so they group.
    assert len(blocks) == 1
    assert blocks[0].text == "noqa\nreal"


def test_python_pragma_inside_grouped_block_drops_only_pragma(tmp_path: Path) -> None:
    """A bare-pragma line in the middle of a block breaks the group; the
    surrounding real comments survive as separate blocks."""
    src = (
        "# real before\n"
        "# noqa\n"
        "# real after\n"
    )
    p = tmp_path / "a.py"
    p.write_text(src)
    blocks = extract_comments(path=p, skip_pragmas=True)
    texts = [b.text for b in blocks]
    # The pragma is dropped; before & after now appear as two
    # separate blocks because the pragma broke the contiguity check.
    assert "real before" in texts
    assert "real after" in texts
    assert "noqa" not in texts


def test_python_malformed_source_degrades(tmp_path: Path) -> None:
    """Syntax error -> tokenize/ast both fail -> we return [], not crash."""
    src = "def broken(:\n    pass\n"
    p = tmp_path / "a.py"
    p.write_text(src)
    # Should not raise.
    blocks = extract_comments(path=p)
    # We may or may not get partial tokenize output; what we MUST NOT do is raise.
    assert isinstance(blocks, list)


# ---------------------------------------------------------------------------
# Java / JS / TS / Go (C-family regex path)
# ---------------------------------------------------------------------------


def test_java_line_and_block_comments(tmp_path: Path) -> None:
    src = (
        "// header line one\n"
        "// header line two\n"
        "public class Foo {\n"
        "    /* block\n"
        "       multi-line */\n"
        "    String s = \"// not a comment\";\n"
        "}\n"
    )
    p = tmp_path / "a.java"
    p.write_text(src)
    blocks = extract_comments(path=p)

    line_blocks = [b for b in blocks if b.kind == "line"]
    block_blocks = [b for b in blocks if b.kind == "block"]
    assert len(line_blocks) == 1
    assert line_blocks[0].text == "header line one\nheader line two"
    assert len(block_blocks) == 1
    assert "multi-line" in block_blocks[0].text


def test_javascript_string_aware_skips_comment_in_string(tmp_path: Path) -> None:
    """``"// not a comment"`` inside a string must not produce a comment."""
    src = (
        "const u = 'http://example.com';\n"
        "const v = \"path with // inside\";\n"
        "// actual comment\n"
    )
    p = tmp_path / "a.js"
    p.write_text(src)
    blocks = extract_comments(path=p)
    # Exactly one comment — the real one.
    assert len(blocks) == 1
    assert blocks[0].text == "actual comment"


def test_javascript_jsdoc_block(tmp_path: Path) -> None:
    src = (
        "/**\n"
        " * Returns the foo.\n"
        " * @param {number} x\n"
        " */\n"
        "function foo(x) { return x; }\n"
    )
    p = tmp_path / "a.js"
    p.write_text(src)
    blocks = extract_comments(path=p)
    assert len(blocks) == 1
    assert blocks[0].kind == "block"
    assert "Returns the foo." in blocks[0].text
    assert "@param" in blocks[0].text


def test_typescript_pragma_dropped_by_default(tmp_path: Path) -> None:
    src = (
        "// @ts-ignore\n"
        "// real comment, long enough to keep around\n"
        "const x: any = 1;\n"
    )
    p = tmp_path / "a.ts"
    p.write_text(src)
    blocks = extract_comments(path=p, skip_pragmas=True)
    # Pragma drops out — but the // @ts-ignore broke contiguity, so
    # we just get the real-comment block.
    texts = [b.text for b in blocks]
    assert "real comment, long enough to keep around" in texts
    assert not any("@ts-ignore" in t for t in texts)


def test_go_block_and_line(tmp_path: Path) -> None:
    src = (
        "// Package foo implements the foo.\n"
        "package foo\n"
        "\n"
        "/* deprecated: use Bar */\n"
        "func Foo() {}\n"
    )
    p = tmp_path / "a.go"
    p.write_text(src)
    blocks = extract_comments(path=p)
    texts = {b.text for b in blocks}
    assert "Package foo implements the foo." in texts
    assert "deprecated: use Bar" in texts


def test_go_build_pragma_dropped(tmp_path: Path) -> None:
    src = (
        "//go:build linux\n"
        "// real architecture note\n"
        "package foo\n"
    )
    p = tmp_path / "a.go"
    p.write_text(src)
    blocks = extract_comments(path=p, skip_pragmas=True)
    texts = [b.text for b in blocks]
    assert "real architecture note" in texts
    assert not any("go:build" in t for t in texts)


# ---------------------------------------------------------------------------
# Rust doc-comments
# ---------------------------------------------------------------------------


def test_rust_doc_comments_strip_extra_slash(tmp_path: Path) -> None:
    """``///`` / ``//!`` doc-comments lose the extra ``/``/``!`` in the body."""
    src = (
        "//! Crate-level docs.\n"
        "/// Item-level doc.\n"
        "/// Continues here.\n"
        "fn foo() {}\n"
    )
    p = tmp_path / "a.rs"
    p.write_text(src)
    blocks = extract_comments(path=p)
    assert len(blocks) == 1
    assert blocks[0].text == "Crate-level docs.\nItem-level doc.\nContinues here."


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------


def test_sql_line_and_block(tmp_path: Path) -> None:
    src = (
        "-- header comment line one\n"
        "-- header comment line two\n"
        "SELECT * FROM t WHERE x = 'a -- b'; -- trailing comment\n"
        "/* block\n"
        "   multi */\n"
    )
    p = tmp_path / "a.sql"
    p.write_text(src)
    blocks = extract_comments(path=p)
    texts = [b.text for b in blocks]
    assert "header comment line one\nheader comment line two" in texts
    # The trailing comment is its own short block — kept because it's >= min_chars default in this test (no filter).
    assert any("trailing comment" in t for t in texts)
    assert any("multi" in t for t in texts)


# ---------------------------------------------------------------------------
# Helpers / language detection
# ---------------------------------------------------------------------------


def test_detect_language_known_and_unknown() -> None:
    assert detect_language(Path("a.py")) == "python"
    assert detect_language(Path("a.tsx")) == "typescript"
    assert detect_language(Path("a.rs")) == "rust"
    assert detect_language(Path("a.unknown")) is None


def test_empty_file_returns_no_blocks(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text("")
    assert extract_comments(path=p) == []


def test_text_override_avoids_filesystem(tmp_path: Path) -> None:
    """Passing ``text=`` lets the caller skip the read entirely."""
    # NB: path doesn't need to exist if text is provided.
    blocks = extract_comments(
        path=Path("nonexistent.py"),
        text="# a real comment\n",
        language="python",
    )
    assert len(blocks) == 1
    assert isinstance(blocks[0], CommentBlock)
    assert blocks[0].text == "a real comment"
