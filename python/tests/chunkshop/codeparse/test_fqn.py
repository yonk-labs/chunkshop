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


# --- Cross-platform path-separator equivalence -----------------------------
#
# Regression suite for the OS-dependency bug in the prior ``Path(...).parts``
# implementation: on a POSIX runner, a Windows-style path collapsed to a
# single segment (``Path("a\\b\\c.py").parts == ("a\\b\\c.py",)``), producing
# an FQN with literal backslashes and a different node_id from the same
# logical path written with forward slashes. Same input, different OS,
# different vector row — silently. The Rust port (RM-C) requires a stable
# spec to match, so all path-separator-equivalent inputs MUST now produce
# identical FQNs regardless of the runtime OS.


def test_windows_and_posix_paths_produce_identical_fqn() -> None:
    """The same logical path written with \\ or / must produce one FQN."""
    posix = build_fqn("a/b/c.py", "f", None)
    windows = build_fqn("a\\b\\c.py", "f", None)
    assert posix == windows == "a.b.c.f"


def test_mixed_separators_normalize_consistently() -> None:
    """A path with both separators (e.g. from naive string concat) normalizes."""
    assert build_fqn("a/b\\c.py", "f", None) == "a.b.c.f"
    assert build_fqn("a\\b/c.py", "f", None) == "a.b.c.f"


def test_leading_separator_is_absorbed_posix() -> None:
    """Leading / is dropped — matches the prior pathlib semantics."""
    assert build_fqn("/a/b/c.py", "f", None) == "a.b.c.f"


def test_leading_separator_is_absorbed_windows() -> None:
    """Leading \\ is dropped — same shape as the POSIX case for parity."""
    assert build_fqn("\\a\\b\\c.py", "f", None) == "a.b.c.f"


def test_consecutive_separators_collapse() -> None:
    """Doubled separators are treated as one — matches pathlib's behaviour."""
    assert build_fqn("a//b/c.py", "f", None) == "a.b.c.f"
    assert build_fqn("a\\\\b\\c.py", "f", None) == "a.b.c.f"


def test_deep_path_keeps_last_three_under_both_separators() -> None:
    """The 3-component window is enforced regardless of separator style."""
    posix = build_fqn("/repo/src/pkg/mod/sub/file.py", "f", None)
    windows = build_fqn("C:\\repo\\src\\pkg\\mod\\sub\\file.py", "f", None)
    assert posix == "mod.sub.file.f"
    # Windows drive letter "C:" survives as a segment but gets windowed out.
    assert windows == "mod.sub.file.f"


def test_method_fqn_invariant_across_separators() -> None:
    """Parent class slotting works regardless of separator style."""
    posix = build_fqn("a/b/c.py", "method", "MyClass")
    windows = build_fqn("a\\b\\c.py", "method", "MyClass")
    assert posix == windows == "a.b.c.MyClass.method"
