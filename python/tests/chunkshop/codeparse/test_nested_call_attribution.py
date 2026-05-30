"""Risk 1: a call inside a nested function must be attributed to the
OUTERMOST emitted function/method, never the nested (never-emitted) one."""
from __future__ import annotations

from chunkshop.codeparse.id import code_symbol_node_id
from chunkshop.codeparse.tree_sitter_wrapper import parse_text


def _symbol_ids(result, lang: str, fp: str) -> set[str]:
    return {
        code_symbol_node_id("default", lang, fp, s.fqn) for s in result.symbols
    }


_PY_NESTED = (
    "def target():\n"
    "    return 1\n"
    "\n"
    "def outer():\n"
    "    def inner():\n"
    "        return target()\n"
    "    return inner()\n"
)


def test_python_nested_call_rolls_up_to_outer() -> None:
    fp = "n.py"
    result = parse_text(_PY_NESTED, language="python", file_path=fp)
    # 'inner' is never emitted (one level deep).
    assert {s.name for s in result.symbols} == {"target", "outer"}
    # The target() call sits in inner(); it must be attributed to outer().
    sym_ids = _symbol_ids(result, "python", fp)
    target_calls = [c for c in result.call_sites if c.callee_name == "target"]
    assert target_calls, "no call to target() captured"
    for c in target_calls:
        assert c.caller_node_id in sym_ids, "orphan caller (attributed to nested fn)"


_PY_METHOD_NESTED = (
    "def freefn():\n"
    "    return 1\n"
    "\n"
    "class C:\n"
    "    def m(self):\n"
    "        def helper():\n"
    "            return freefn()\n"
    "        return helper()\n"
)


def test_python_nested_in_method_rolls_up_to_method() -> None:
    fp = "m.py"
    result = parse_text(_PY_METHOD_NESTED, language="python", file_path=fp)
    sym_ids = _symbol_ids(result, "python", fp)
    free_calls = [c for c in result.call_sites if c.callee_name == "freefn"]
    assert free_calls
    for c in free_calls:
        assert c.caller_node_id in sym_ids


_TS_NESTED = (
    "function target() { return 1; }\n"
    "function outer() {\n"
    "  function inner() { return target(); }\n"
    "  return inner();\n"
    "}\n"
)


def test_typescript_nested_call_rolls_up_to_outer() -> None:
    fp = "n.ts"
    result = parse_text(_TS_NESTED, language="typescript", file_path=fp)
    sym_ids = _symbol_ids(result, "typescript", fp)
    target_calls = [c for c in result.call_sites if c.callee_name == "target"]
    assert target_calls
    for c in target_calls:
        assert c.caller_node_id in sym_ids


def test_javascript_nested_call_rolls_up_to_outer() -> None:
    fp = "n.js"
    result = parse_text(_TS_NESTED, language="javascript", file_path=fp)
    sym_ids = _symbol_ids(result, "javascript", fp)
    target_calls = [c for c in result.call_sites if c.callee_name == "target"]
    assert target_calls
    for c in target_calls:
        assert c.caller_node_id in sym_ids
