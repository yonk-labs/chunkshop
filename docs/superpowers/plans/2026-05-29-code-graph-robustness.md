# Code-Graph Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate and fix the five code-graph robustness risks logged during RM-C parity validation — primarily the orphan-edge bug where calls inside nested functions are attributed to never-emitted symbols — and add the corpus-scale invariant test that guards them.

**Architecture:** `codeparse/langs/*.py` extractors emit `Symbol`s ("one level deep" — nested functions are not emitted) and `CallSite`s whose `caller_node_id` comes from `_enclosing_function`. Today `_enclosing_function` returns the *innermost* enclosing function, so a call inside a nested function gets a caller node that was never emitted → orphan edge. The fix returns the *outermost* enclosing function (the emitted one), mirroring `_walk_symbols`' emission rule. A new corpus-scale test asserts no orphan callers / in-bounds spans across chunkshop's own source tree.

**Tech Stack:** Python 3.12, tree-sitter (`[code]` extra), pytest. No schema/DB change.

**Spec:** `docs/superpowers/specs/2026-05-29-code-graph-robustness-design.md` (sub-project B). Sub-projects A (new languages) and C (import-aware resolution) are out of scope.

**Working dir:** all paths are relative to `python/` in the `feat/code-graph-robustness` worktree unless noted. Run tests with `uv run --no-sync pytest …` (the `--no-sync` preserves the `[code]` extra).

---

### Task 1: Corpus-scale invariant test (Risk 4) — the failing net that proves Risk 1

**Files:**
- Create: `python/tests/chunkshop/codeparse/test_corpus_invariants.py`

This test parses chunkshop's own `src/chunkshop/**/*.py` tree and asserts the
graph invariants. It FAILS on `main` today because nested-function calls
produce orphan caller node_ids (Risk 1). Task 2 makes it pass.

- [ ] **Step 1: Write the test**

Create `python/tests/chunkshop/codeparse/test_corpus_invariants.py`:

```python
"""Corpus-scale invariant tests for the codeparse layer.

Parses chunkshop's own source tree (hundreds of real symbols: nested
functions, decorators, methods) and asserts graph invariants that the tiny
per-language fixtures cannot reach. This is the regression net for the
orphan-edge bug (Risk 1) and span correctness (Risk 2).

Gated on the [code] extra — skips cleanly when tree-sitter isn't installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_python")

import chunkshop
from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id

_SRC_ROOT = Path(chunkshop.__file__).resolve().parent


def _python_corpus() -> list[Path]:
    files = sorted(_SRC_ROOT.rglob("*.py"))
    assert len(files) > 30, f"corpus too small ({len(files)}); wrong root?"
    return files


def test_no_orphan_caller_nodes() -> None:
    """Every call site's caller_node_id must be an emitted symbol's node_id.

    A caller_node_id with no matching symbol means the edge's source node
    doesn't exist — the orphan-edge bug. project_id/language/file_path here
    mirror exactly what the extractor used to mint caller_node_id.
    """
    offenders: list[str] = []
    for path in _python_corpus():
        result = parse_file(path, language="python")
        lang = result.language or "python"
        fp = str(path)
        symbol_ids = {
            code_symbol_node_id("default", lang, fp, s.fqn)
            for s in result.symbols
        }
        for cs in result.call_sites:
            if cs.caller_node_id not in symbol_ids:
                offenders.append(f"{path.name}: {cs.caller_node_id} ({cs.callee_name} @L{cs.line})")
    assert not offenders, "orphan caller node_ids:\n" + "\n".join(offenders[:25])


def test_spans_in_bounds() -> None:
    """1 <= line_start <= line_end <= len(file_lines) for every symbol."""
    offenders: list[str] = []
    for path in _python_corpus():
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        result = parse_file(path, language="python")
        for s in result.symbols:
            if not (1 <= s.line_start <= s.line_end <= max(n_lines, 1)):
                offenders.append(
                    f"{path.name}: {s.fqn} span=({s.line_start},{s.line_end}) file_lines={n_lines}"
                )
    assert not offenders, "out-of-bounds spans:\n" + "\n".join(offenders[:25])


def test_parse_never_raises() -> None:
    """parse_file is best-effort: it must not raise on any corpus file."""
    for path in _python_corpus():
        parse_file(path, language="python")  # must not raise


def test_node_ids_deterministic() -> None:
    """Re-parsing a file yields identical (fqn, node_id) sets."""
    sample = _python_corpus()[0]

    def ids(p: Path) -> set[tuple[str, str]]:
        r = parse_file(p, language="python")
        return {
            (s.fqn, code_symbol_node_id("default", r.language or "python", str(p), s.fqn))
            for s in r.symbols
        }

    assert ids(sample) == ids(sample)
```

- [ ] **Step 2: Run the test to verify the orphan invariant FAILS**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse/test_corpus_invariants.py -v`
Expected: `test_no_orphan_caller_nodes` **FAILS** listing orphan caller node_ids
(calls inside nested functions such as `symbol_aware.py`'s `visit`). The other
three tests PASS. This failure is the validation of Risk 1.

- [ ] **Step 3: Commit the (failing) test**

```bash
git add tests/chunkshop/codeparse/test_corpus_invariants.py
git commit -m "test(codeparse): corpus-scale graph invariants (Risk 4) — orphan check fails pre-fix"
```

---

### Task 2: Fix orphan-edge attribution — outermost enclosing function (Risk 1)

**Files:**
- Modify: `python/src/chunkshop/codeparse/langs/python.py:131-157` (`_enclosing_function`)
- Modify: `python/src/chunkshop/codeparse/langs/typescript.py:148-170` (`_enclosing_function`; also fixes javascript.py, which imports it)
- Test: `python/tests/chunkshop/codeparse/test_nested_call_attribution.py` (create)

- [ ] **Step 1: Write the failing per-language unit tests**

Create `python/tests/chunkshop/codeparse/test_nested_call_attribution.py`:

```python
"""Risk 1: a call inside a nested function must be attributed to the
OUTERMOST emitted function/method, never the nested (never-emitted) one."""
from __future__ import annotations

from chunkshop.codeparse import parse_file, parse_text
from chunkshop.codeparse.id import code_symbol_node_id


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse/test_nested_call_attribution.py -v`
Expected: all four FAIL — the nested call's `caller_node_id` is minted from
the nested function name (`inner`/`helper`), which is not an emitted symbol.

- [ ] **Step 3: Rewrite `_enclosing_function` in `python.py`**

Replace the whole function at `python.py:131-157` with:

```python
def _enclosing_function(node: Any, source: bytes) -> Optional[tuple[str, Optional[str]]]:
    """Return the OUTERMOST enclosing function/method as (name, parent_class).

    Mirrors :func:`_walk_symbols`' "one level deep" emission rule: nested
    functions are never emitted as Symbols, so a call inside one must be
    attributed to the outermost enclosing function (the symbol that WAS
    emitted), not the innermost. Returns None for a module-level call (no
    enclosing function — there is no symbol to attribute to).
    """
    cur = node.parent
    outermost: Optional[Any] = None
    while cur is not None:
        if cur.type == "function_definition":
            outermost = cur  # keep climbing; the last one wins (highest)
        cur = cur.parent
    if outermost is None:
        return None
    name_node = outermost.child_by_field_name("name")
    if name_node is None:
        return None
    func_name = source[name_node.start_byte : name_node.end_byte].decode(
        errors="replace"
    )
    # parent_class = nearest class_definition ancestor of the outermost
    # function. Because the outermost function has no function ancestor, any
    # enclosing class makes it a method (matching _walk_symbols).
    parent_class: Optional[str] = None
    anc = outermost.parent
    while anc is not None:
        if anc.type == "class_definition":
            cname = anc.child_by_field_name("name")
            if cname is not None:
                parent_class = source[
                    cname.start_byte : cname.end_byte
                ].decode(errors="replace")
            break
        anc = anc.parent
    return (func_name, parent_class)
```

- [ ] **Step 4: Rewrite `_enclosing_function` in `typescript.py`**

Replace the whole function at `typescript.py:148-170` with:

```python
def _enclosing_function(
    node: Any, source: bytes
) -> Optional[tuple[str, Optional[str]]]:
    """Return the OUTERMOST enclosing function/method as (name, parent_class).

    Same "one level deep" rule as python.py: a call inside a nested
    function_declaration must roll up to the outermost emitted symbol, never
    the nested one. Shared by javascript.py via import.
    """
    cur = node.parent
    outermost: Optional[Any] = None
    while cur is not None:
        if cur.type in ("function_declaration", "method_definition"):
            outermost = cur  # keep climbing; the last one wins (highest)
        cur = cur.parent
    if outermost is None:
        return None
    name_node = outermost.child_by_field_name("name")
    if name_node is None:
        return None
    func_name = _text(name_node, source)
    parent: Optional[str] = None
    if outermost.type == "method_definition":
        anc = outermost.parent
        while anc is not None:
            if anc.type == "class_declaration":
                cn = anc.child_by_field_name("name")
                parent = _text(cn, source) if cn is not None else None
                break
            anc = anc.parent
    return (func_name, parent)
```

- [ ] **Step 5: Run the unit tests + the corpus test to verify they pass**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse/test_nested_call_attribution.py tests/chunkshop/codeparse/test_corpus_invariants.py -v`
Expected: all PASS — including `test_no_orphan_caller_nodes`, now that callers
roll up to emitted symbols.

- [ ] **Step 6: Run the existing codeparse + relationships suites for regressions**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse tests/chunkshop/test_code_relationships_extractor.py tests/chunkshop/test_symbol_aware_chunker.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/chunkshop/codeparse/langs/python.py src/chunkshop/codeparse/langs/typescript.py tests/chunkshop/codeparse/test_nested_call_attribution.py
git commit -m "fix(codeparse): attribute nested-function calls to outermost emitted symbol (Risk 1)"
```

---

### Task 3: Include decorators in symbol spans + boundary span tests (Risk 2)

**Files:**
- Modify: `python/src/chunkshop/codeparse/langs/python.py:85-122` (`_walk_symbols` — class + function span)
- Test: `python/tests/chunkshop/codeparse/test_python_spans.py` (create)

- [ ] **Step 1: Write the failing span tests**

Create `python/tests/chunkshop/codeparse/test_python_spans.py`:

```python
"""Risk 2: symbol spans must include decorators and be correct at file edges."""
from __future__ import annotations

from chunkshop.codeparse import parse_text

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
```

- [ ] **Step 2: Run to verify the decorator tests fail**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse/test_python_spans.py -v`
Expected: `test_decorated_function_span_includes_decorators` and
`test_decorated_class_span_includes_decorator` FAIL (line_start == 5 / 4, the
`def`/`class` line, because the decorator lines are dropped).
`test_symbol_span_at_end_of_file_is_in_bounds` PASSES.

- [ ] **Step 3: Use the decorated_definition wrapper for the span**

In `python.py` `_walk_symbols`, both the `class_definition` branch
(lines ~85-103) and the `function_definition` branch (lines ~104-122) compute
`line_start=node.start_point[0] + 1` / `line_end=node.end_point[0] + 1`. When
the node's parent is a `decorated_definition`, the span must start at the
decorator. Introduce a tiny local helper at the top of `_walk_symbols` (just
inside the function, before `def visit`):

```python
    def _span(node: Any) -> tuple[int, int]:
        """1-based inclusive span, widened to the decorator block if any."""
        span_node = node
        parent = node.parent
        if parent is not None and parent.type == "decorated_definition":
            span_node = parent
        return (span_node.start_point[0] + 1, span_node.end_point[0] + 1)
```

Then in the `class_definition` branch replace:

```python
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
```

with:

```python
                        line_start=_span(node)[0],
                        line_end=_span(node)[1],
```

and make the identical replacement in the `function_definition` branch.

- [ ] **Step 4: Run the span tests to verify they pass**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse/test_python_spans.py -v`
Expected: all three PASS.

- [ ] **Step 5: Re-run the corpus invariants (spans must stay in-bounds)**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse/test_corpus_invariants.py tests/chunkshop/codeparse/test_parse_python.py -q`
Expected: all PASS — widening to the decorator never pushes line_start below 1
or above line_end.

- [ ] **Step 6: Commit**

```bash
git add src/chunkshop/codeparse/langs/python.py tests/chunkshop/codeparse/test_python_spans.py
git commit -m "fix(codeparse): include decorators in python symbol spans (Risk 2)"
```

---

### Task 4: Realistic per-language fixtures + exact-count assertions (Risk 3)

**Files:**
- Create: `python/tests/fixtures/codeparse/python/realistic.py`
- Create: `python/tests/fixtures/codeparse/typescript/realistic.ts`
- Test: `python/tests/chunkshop/codeparse/test_realistic_fixtures.py` (create)

Existing `sample.*` fixtures stay (other tests reference them). We add a
realistic module per language exercising nesting + decorators, and assert exact
symbol/edge attribution on it.

- [ ] **Step 1: Create the Python realistic fixture**

Create `python/tests/fixtures/codeparse/python/realistic.py`:

```python
"""Realistic fixture: nesting, a decorator, a method calling a free function."""
from __future__ import annotations

import functools


def load(raw: str) -> int:
    return int(raw)


@functools.lru_cache(maxsize=8)
def cached_double(n: int) -> int:
    return n * 2


class Pipeline:
    def run(self, raw: str) -> int:
        def step(v: int) -> int:
            return cached_double(v)

        value = load(raw)
        return step(value)
```

- [ ] **Step 2: Create the TypeScript realistic fixture**

Create `python/tests/fixtures/codeparse/typescript/realistic.ts`:

```typescript
export function load(raw: string): number {
  return parseInt(raw, 10);
}

export class Pipeline {
  run(raw: string): number {
    function step(v: number): number {
      return load(v);
    }
    return step(load(raw));
  }
}
```

- [ ] **Step 3: Write the assertion test**

Create `python/tests/chunkshop/codeparse/test_realistic_fixtures.py`:

```python
"""Risk 3: realistic fixtures with nesting + decorators, exact attribution."""
from __future__ import annotations

from pathlib import Path

from chunkshop.codeparse import parse_file
from chunkshop.codeparse.id import code_symbol_node_id


def _sym_ids(result, lang: str, fp: str) -> set[str]:
    return {code_symbol_node_id("default", lang, fp, s.fqn) for s in result.symbols}


def test_python_realistic_no_orphans_and_decorator_span(fixtures_dir: Path) -> None:
    path = fixtures_dir / "python" / "realistic.py"
    result = parse_file(path, language="python")

    names = {s.name for s in result.symbols}
    # 'step' is nested in run() -> never emitted.
    assert "step" not in names
    assert {"load", "cached_double", "Pipeline", "run"} <= names

    # The decorator widens cached_double's span to its @-line.
    cd = next(s for s in result.symbols if s.name == "cached_double")
    line = path.read_text().splitlines()
    assert line[cd.line_start - 1].lstrip().startswith("@")

    # No orphan callers (cached_double() is called from nested step()).
    ids = _sym_ids(result, "python", str(path))
    for c in result.call_sites:
        assert c.caller_node_id in ids


def test_typescript_realistic_no_orphans(fixtures_dir: Path) -> None:
    path = fixtures_dir / "typescript" / "realistic.ts"
    result = parse_file(path, language="typescript")
    names = {s.name for s in result.symbols}
    assert "step" not in names  # nested fn not emitted
    ids = _sym_ids(result, "typescript", str(path))
    for c in result.call_sites:
        assert c.caller_node_id in ids
```

- [ ] **Step 4: Run the fixture tests**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse/test_realistic_fixtures.py -v`
Expected: all PASS (post Task 2 + Task 3 fixes).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/codeparse/python/realistic.py tests/fixtures/codeparse/typescript/realistic.ts tests/chunkshop/codeparse/test_realistic_fixtures.py
git commit -m "test(codeparse): realistic fixtures exercising nesting + decorators (Risk 3)"
```

---

### Task 5: Changelog + full-suite regression + drift check

**Files:**
- Modify: `CHANGELOG.md` (under `## Unreleased`)

- [ ] **Step 1: Add the changelog entry**

Under `## Unreleased` → `### Fixed` (create the heading if absent), add:

```markdown
- **`codeparse`: calls inside nested functions now attribute to the outermost emitted symbol (Risk 1).** Previously `_enclosing_function` returned the innermost function, so a call inside a nested function produced a `CALLS` edge whose `caller_node_id` referenced a symbol that was never emitted (an orphan edge source). Fixed for Python and the ECMAScript family (TypeScript + JavaScript). Go/Java were already structurally safe.
- **`codeparse`: Python symbol spans now include decorator lines (Risk 2).** A decorated `def`/`class` previously began at the `def`/`class` line, dropping `@decorator` lines from the symbol's `original_content` and `start_line` metadata. The span now starts at the first decorator.
- Added a corpus-scale invariant test (no orphan callers, in-bounds spans, no parse crashes, deterministic node_ids) over chunkshop's own source tree, plus realistic per-language fixtures.
```

- [ ] **Step 2: Run the full codeparse-adjacent suite**

Run: `uv run --no-sync pytest tests/chunkshop/codeparse tests/chunkshop/test_code_relationships_extractor.py tests/chunkshop/test_symbol_aware_chunker.py tests/chunkshop/test_cli_search_by_symbol.py -q`
Expected: all PASS.

- [ ] **Step 3: Run the whole test suite to confirm no wider regression**

Run: `uv run --no-sync pytest -q`
Expected: PASS (DB-gated tests skip without DSNs — acceptable).

- [ ] **Step 4: Drift check against the spec**

Confirm:
- Risk 1 fixed + tested (Tasks 1, 2) ✅
- Risk 2 fixed + tested (Task 3) ✅
- Risk 3 realistic fixtures added (Task 4) ✅
- Risk 4 corpus invariant test added + guarding (Task 1) ✅
- Risk 5 (missing languages) NOT touched — deferred to sub-project A ✅
- No schema / `edge_kind` / `provenance` change ✅
- No nested functions emitted as new symbols (contract preserved) ✅

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): code-graph robustness fixes (Risks 1-4)"
```

---

## Self-Review

**Spec coverage:** Risk 1 → Tasks 1+2; Risk 2 → Task 3; Risk 3 → Task 4; Risk 4 → Task 1; Risk 5 explicitly deferred. Every in-scope spec requirement maps to a task. ✅

**Placeholder scan:** No TBD/TODO. Every code step shows complete code; every run step shows the command + expected result. ✅

**Type/name consistency:** `_enclosing_function` returns `(name, parent_class)` in both languages, consumed identically by each module's existing `_extract_call_sites` (which builds `caller_fqn = build_fqn(file_path, func_name, parent)` then `code_symbol_node_id`). `code_symbol_node_id("default", lang, fp, fqn)` signature matches `id.py`. `parse_text(content, language=, file_path=)` and `parse_file(path, language=)` signatures match `tree_sitter_wrapper.py`. `_span` helper name is introduced and used only within Task 3. ✅

**Boundary correctness:** Module-level calls still return `None` from `_enclosing_function` (skipped, unchanged). The `[code]`-extra gate (`importorskip`) keeps the corpus test green-or-skipped without tree-sitter. Decorator widening can only move `line_start` upward (earlier line), never below 1 or above `line_end`. ✅
