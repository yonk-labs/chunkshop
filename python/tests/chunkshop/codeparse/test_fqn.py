"""Tests for build_fqn — deterministic FQN composition."""
from __future__ import annotations

from chunkshop.codeparse import build_fqn


def test_simple_function_in_short_path() -> None:
    """Single-segment paths use the bare stem."""
    assert build_fqn("c.py", "f", None) == "c.f"


def test_function_in_three_segment_path() -> None:
    """Three-segment paths keep all three segments minus the extension."""
    assert build_fqn("a/b/c.py", "f", None) == "a.b.c.f"


def test_function_in_deep_path_keeps_only_last_three() -> None:
    """Deep paths collapse to the last three components — intentional."""
    assert (
        build_fqn("/repo/src/pkg/mod/sub/file.py", "f", None)
        == "mod.sub.file.f"
    )


def test_method_with_parent_class() -> None:
    """Parent class slots between path prefix and symbol name."""
    assert build_fqn("a/b/c.py", "method", "MyClass") == "a.b.c.MyClass.method"


def test_no_parent_when_explicit_none() -> None:
    """parent_name=None produces a non-method FQN even if the symbol IS a method."""
    assert build_fqn("a/b/c.py", "f", None) == "a.b.c.f"


def test_handles_extension_only_in_last_segment() -> None:
    """Only the file's extension is stripped, not dots elsewhere in path."""
    # "a.b" looks like file.ext but isn't the last segment — stays put.
    assert build_fqn("repo/a.b/file.ts", "g", None) == "repo.a.b.file.g"


def test_distinct_inputs_produce_distinct_fqns() -> None:
    """Different files / parents / names must not collide."""
    fqns = {
        build_fqn("a/b/c.py", "f", None),
        build_fqn("a/b/c.py", "g", None),
        build_fqn("a/b/d.py", "f", None),
        build_fqn("a/b/c.py", "f", "C"),
    }
    assert len(fqns) == 4
