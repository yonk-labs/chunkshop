"""parse_text helper test: in-memory source must round-trip like parse_file.

Added in SP-C so the ``code_relationships`` extractor can parse chunk-body
strings without writing them to a tempfile.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.tree_sitter_wrapper import parse_text


_PY = """\
def alpha():
    return 1


class Box:
    def open(self):
        return 2
"""


def test_parse_text_returns_python_symbols() -> None:
    result = parse_text(_PY, language="python", file_path="mem.py")
    names = {s.name for s in result.symbols}
    # Top-level function, class, and its method should all be present.
    assert {"alpha", "Box", "open"} <= names


def test_parse_text_matches_parse_file_on_same_content() -> None:
    """parse_text and parse_file must agree on symbol set for the same source."""
    in_memory = parse_text(_PY, language="python", file_path="same.py")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(_PY)
        path = Path(f.name)
    try:
        on_disk = parse_file(path, language="python")
    finally:
        path.unlink(missing_ok=True)
    assert {s.name for s in in_memory.symbols} == {s.name for s in on_disk.symbols}


def test_parse_text_handles_unknown_language_gracefully() -> None:
    """Unknown language returns an empty ParseResult, never raises."""
    result = parse_text("anything", language="unknown", file_path="x.unk")
    assert result.symbols == []
    assert result.call_sites == []
