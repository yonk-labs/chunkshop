"""Tests for code_symbol_node_id — deterministic, project-scoped IDs."""
from __future__ import annotations

from chunkshop.codeparse import code_symbol_node_id


def test_deterministic_for_same_inputs() -> None:
    """Two calls with identical args produce identical IDs."""
    a = code_symbol_node_id("proj", "python", "/x/y.py", "y.helper")
    b = code_symbol_node_id("proj", "python", "/x/y.py", "y.helper")
    assert a == b


def test_id_format_is_node_prefix_plus_16_hex() -> None:
    """The contract: 'node-' + 16 lowercase hex chars."""
    nid = code_symbol_node_id("proj", "python", "/x/y.py", "y.helper")
    assert nid.startswith("node-")
    suffix = nid[len("node-") :]
    assert len(suffix) == 16
    assert all(c in "0123456789abcdef" for c in suffix)


def test_different_projects_diverge() -> None:
    """Same symbol in two projects must produce different IDs."""
    a = code_symbol_node_id("proj_a", "python", "/x/y.py", "y.helper")
    b = code_symbol_node_id("proj_b", "python", "/x/y.py", "y.helper")
    assert a != b


def test_different_languages_diverge() -> None:
    """Same FQN in different languages must produce different IDs."""
    a = code_symbol_node_id("proj", "python", "/x/y.py", "y.helper")
    b = code_symbol_node_id("proj", "java", "/x/y.py", "y.helper")
    assert a != b


def test_different_files_diverge() -> None:
    """Same FQN in different files must produce different IDs."""
    a = code_symbol_node_id("proj", "python", "/x/y.py", "y.helper")
    b = code_symbol_node_id("proj", "python", "/x/z.py", "y.helper")
    assert a != b


def test_different_fqns_diverge() -> None:
    """Same file, different FQN -> different IDs."""
    a = code_symbol_node_id("proj", "python", "/x/y.py", "y.helper")
    b = code_symbol_node_id("proj", "python", "/x/y.py", "y.Other.helper")
    assert a != b
