"""Risk 2: symbol spans must include decorators and be correct at file edges."""
from __future__ import annotations

from chunkshop.codeparse.tree_sitter_wrapper import parse_text

_DECORATED = (
    "import functools\n"
    "\n"
    "@functools.cache\n"
    "@staticmethod\n"
    "def decorated():\n"
    "    return 1\n"
)


def test_decorated_function_span_includes_decorators() -> None:
    """line_start must point at the first @decorator, not the def line."""
    result = parse_text(_DECORATED, language="python", file_path="d.py")
    fn = next(s for s in result.symbols if s.name == "decorated")
    # @functools.cache is line 3; def is line 5.
    assert fn.line_start == 3, f"expected decorator line 3, got {fn.line_start}"
    assert fn.line_end == 6


_LAST_LINE = "def only():\n    return 1"  # no trailing newline, ends file


def test_symbol_span_at_end_of_file_is_in_bounds() -> None:
    result = parse_text(_LAST_LINE, language="python", file_path="e.py")
    fn = next(s for s in result.symbols if s.name == "only")
    n_lines = len(_LAST_LINE.splitlines())  # == 2
    assert 1 <= fn.line_start <= fn.line_end <= n_lines


_DECORATED_CLASS = (
    "import dataclasses\n"
    "\n"
    "@dataclasses.dataclass\n"
    "class Point:\n"
    "    x: int\n"
    "    y: int\n"
)


def test_decorated_class_span_includes_decorator() -> None:
    result = parse_text(_DECORATED_CLASS, language="python", file_path="p.py")
    cls = next(s for s in result.symbols if s.name == "Point")
    assert cls.line_start == 3  # @dataclasses.dataclass
