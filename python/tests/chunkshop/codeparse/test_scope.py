"""Tests for build_scope_chain — human-readable enclosing-scope path.

``scope_chain`` is the display-string companion to ``fqn``: ``fqn`` is the
machine-readable join key (``a.b.c.MyClass.method``), ``scope_chain`` is the
UI/search-result string (``c > MyClass > method``). It uses the file *stem*
only — the full path already lives in ``fqn`` and ``file_path``. Mirrors the
``test_fqn.py`` case set so the two helpers stay in lockstep.
"""
from __future__ import annotations

from chunkshop.codeparse import build_scope_chain


def test_top_level_function_uses_stem_and_name() -> None:
    """A top-level function renders ``stem > name``."""
    assert build_scope_chain("pkg/utils.py", "format", None) == "utils > format"


def test_method_slots_parent_between_stem_and_name() -> None:
    """A method renders ``stem > parent > name``."""
    assert (
        build_scope_chain("users/svc.py", "get_user", "UserService")
        == "svc > UserService > get_user"
    )


def test_short_path_uses_bare_stem() -> None:
    """Single-segment paths still use the bare stem."""
    assert build_scope_chain("c.py", "f", None) == "c > f"


def test_deep_path_uses_only_file_stem() -> None:
    """Unlike fqn, scope_chain keeps ONLY the file stem — no path prefix."""
    assert (
        build_scope_chain("/repo/src/pkg/mod/sub/file.py", "f", None)
        == "file > f"
    )


def test_separator_is_space_arrow_space() -> None:
    """The separator is ' > ' for readability."""
    chain = build_scope_chain("a/b/c.py", "m", "C")
    assert chain == "c > C > m"
    assert " > " in chain


def test_extension_stripped_from_stem_only() -> None:
    """Only the file extension is stripped, dots elsewhere in path don't matter."""
    assert build_scope_chain("repo/a.b/file.ts", "g", None) == "file > g"


# --- Cross-platform path-separator equivalence -----------------------------
#
# scope_chain feeds the same cross-port-equivalence discipline as fqn (the
# Rust RM-C port asserts parity), so path-separator-equivalent inputs MUST
# produce identical scope_chains regardless of the runtime OS.


def test_windows_and_posix_paths_produce_identical_scope_chain() -> None:
    posix = build_scope_chain("a/b/c.py", "f", None)
    windows = build_scope_chain("a\\b\\c.py", "f", None)
    assert posix == windows == "c > f"


def test_mixed_separators_normalize_consistently() -> None:
    assert build_scope_chain("a/b\\c.py", "f", None) == "c > f"
    assert build_scope_chain("a\\b/c.py", "f", None) == "c > f"


def test_windows_drive_path_uses_file_stem() -> None:
    assert (
        build_scope_chain("C:\\repo\\src\\file.py", "f", None) == "file > f"
    )


def test_method_scope_chain_invariant_across_separators() -> None:
    posix = build_scope_chain("a/b/c.py", "method", "MyClass")
    windows = build_scope_chain("a\\b\\c.py", "method", "MyClass")
    assert posix == windows == "c > MyClass > method"


def test_synthetic_module_symbol_renders_cleanly() -> None:
    """Synthetic <module> / <module_block> symbols still produce a valid chain."""
    assert build_scope_chain("a/b/c.py", "<module>", None) == "c > <module>"
    assert (
        build_scope_chain("a/b/c.py", "<module_block>", None)
        == "c > <module_block>"
    )
