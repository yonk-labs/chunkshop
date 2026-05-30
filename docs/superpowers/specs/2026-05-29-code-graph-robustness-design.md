# Code-Graph Robustness — Design Spec (Sub-project B)

**Date:** 2026-05-29
**Branch / worktree:** `feat/code-graph-robustness` (`../chunkshop-code-graph-robustness`, off `main`)
**Status:** approved-by-autonomy (user directed "continue autonomously")

## Context

chunkshop's code graph is produced by two cooperating layers:

- **`codeparse/`** — per-language tree-sitter extractors (`langs/python.py`,
  `java.py`, `go.py`, `typescript.py`, `javascript.py`) returning a
  `ParseResult(symbols, call_sites, imports, …)`. Each `Symbol` carries an FQN
  and a 1-based inclusive `(line_start, line_end)` span; each `CallSite`
  carries a `caller_node_id` minted from the *enclosing* function's FQN.
- **`chunkers/symbol_aware.py`** — slices source at symbol boundaries using
  those spans, and the **`extractors/code_relationships.py`** extractor turns
  `call_sites` into `CALLS` / `INHERITS` / `IMPLEMENTS` edges in the
  `code_edges` table.

During the RM-C corpus-parity validation (2026-05-29), five robustness
**risks** were logged but never validated. This sub-project validates each,
fixes the ones that are real, and — most importantly — adds the corpus-scale
invariant test that would have caught them automatically. This hardening lands
**before** sub-project A (Rust / C / C++ / C# / Ruby extractors) precisely so
the pattern is correct *before* it's replicated across five new languages.

This is purely Python-path work. No Rust port, no schema change, no new
`edge_kind` / `provenance` values.

## The five risks (validated scope)

| # | Risk | Verdict from code inspection | In scope for B? |
|---|------|------------------------------|-----------------|
| 1 | **Nested-function call attribution → orphan edges** | **Real bug.** `_walk_symbols` emits functions "one level deep" (nested functions are *not* emitted as symbols), but `_enclosing_function` attributes a call to the *innermost* `function_definition` / `function_declaration`. A call inside a nested function therefore gets a `caller_node_id` for a symbol that was never emitted → an edge whose source node has no corresponding node. Confirmed in `python.py` and `typescript.py`; `javascript.py` shares the shape. Go is structurally safe (nested funcs are `func_literal`, not declarations). Java has no nested method declarations. | **Yes — primary fix** |
| 2 | **Symbol span fidelity (`line_start` / `line_end`)** | **Partly real.** Python decorators live in a `decorated_definition` wrapper; `_walk_symbols` matches the inner `function_definition`, so `line_start` begins at `def` and the `@decorator` lines are dropped from the symbol's `original_content`. `line_end` from `end_point[0]+1` is correct for the cases inspected but is untested at boundaries (last-line symbol, trailing newline). | **Yes — validate + fix decorators** |
| 3 | **Tiny fixtures** | **Real.** Every `tests/fixtures/codeparse/<lang>/` fixture is ~200–500 bytes (2–3 trivial symbols, no nesting, no decorators). They cannot exercise risks 1–2. | **Yes — add realistic fixtures** |
| 4 | **No corpus-scale test** | **Real.** Nothing parses a non-trivial body of code and asserts graph invariants. This is the regression net that makes risks 1–2 *stay* fixed and de-risks sub-project A. | **Yes — centerpiece** |
| 5 | **Missing languages** | Real, but this **is** sub-project A. | **No — deferred to A** |

## Goals / Non-goals

**Goals**
- No `CALLS` edge may reference a `caller_node_id` that is not the node_id of an emitted symbol (no orphan edge sources), across every supported language.
- Symbol spans faithfully include decorators (Python) and are correct at file boundaries.
- A corpus-scale invariant test guards both, runs in CI with the `[code]` extra, and skips cleanly without it.
- Realistic fixtures that contain nested functions, decorators, methods, and multi-line bodies.

**Non-goals**
- Adding languages (sub-project A).
- Import-aware cross-file resolution (sub-project C / Python read of #42).
- Emitting nested functions as their own symbols. We keep the "one level deep"
  contract; we only fix *attribution* so calls roll up to the emitted parent.
- Any change to `code_edges` schema, `edge_kind`, or `provenance`.

## Design

### Fix 1 — attribute calls to the outermost emitted function

`_enclosing_function` currently returns the **innermost** enclosing function.
Change it to return the **outermost** enclosing function (the one with no
function ancestor) plus that function's directly-enclosing class (if any).
This exactly mirrors `_walk_symbols`' emission rule:

- A call inside `def outer(): def inner(): foo()` → caller is `outer` (emitted),
  not `inner` (never emitted).
- A call inside `class C: def m(self): def inner(): foo()` → caller is `C.m`.
- A module-level call (no enclosing function) → still skipped (unchanged; there
  is no symbol to attribute to, and minting a `<module>` caller is out of scope).

Algorithm: walk ancestors to the root; record the **last** (highest)
`function_definition`/`function_declaration`/`method_definition` seen; its
`parent_class` is the nearest `class_definition`/`class_declaration` ancestor of
*that* node. Because the outermost function has no function ancestor, any class
ancestor is a legitimate method parent.

Apply to `python.py`, `typescript.py`, `javascript.py`. Add a regression test
per language. Leave `go.py` / `java.py` unchanged but cover them with the
corpus invariant test to confirm they stay orphan-free.

### Fix 2 — include decorators in the Python symbol span

When a `function_definition` (or `class_definition`) is the child of a
`decorated_definition`, take `line_start` from the `decorated_definition` node
so the `@decorator` lines are part of the symbol's `original_content`. Validate
`line_end` at boundaries (last-line symbol, no trailing newline, blank trailing
lines) with explicit tests; fix only if a test fails. No speculative change to
`_slice_lines`.

### Fix 4 — corpus-scale invariant test (centerpiece)

New `tests/chunkshop/codeparse/test_corpus_invariants.py`. Corpus = chunkshop's
own `python/src/chunkshop/**/*.py` tree (real, in-repo, no network, hundreds of
symbols), plus the realistic per-language fixtures from Fix 3. For every parsed
file assert:

1. **No orphan callers** — every `call_site.caller_node_id` equals the node_id
   of some emitted symbol in the same file (node_id rebuilt with the same
   `code_symbol_node_id(project_id, lang, file_path, fqn)` recipe the symbols
   use). *This assertion fails on `main` today — it is the failing test that
   proves Risk 1.*
2. **In-bounds spans** — `1 <= line_start <= line_end <= len(file_lines)`.
3. **No crashes** — `parse_file` never raises on any corpus file.
4. **Deterministic node_ids** — re-parsing the same file yields identical
   `(fqn, node_id)` sets.

Gated on the `[code]` extra: `pytest.importorskip("tree_sitter_python")` so it
skips cleanly when the extra is absent (matching the repo's skip-not-fail
posture). Test the chunkshop-src corpus for Python; the smaller realistic
fixtures cover Go/Java/TS/JS invariants.

### Fix 3 — realistic fixtures

Augment (do not delete — existing tests reference them) the per-language
fixtures with one realistic module each (`realistic.<ext>`) containing: a
top-level function that calls another, a class with methods where a method
calls a free function, a **nested function that makes a call** (the Risk-1
trigger), and — for Python — a **decorated** function/method (the Risk-2
trigger). Keep them small enough to assert exact symbol/edge counts.

## Components & boundaries

- `codeparse/langs/python.py` — `_enclosing_function` (Fix 1), `_walk_symbols`
  decorator span (Fix 2). Pure functions, tested directly.
- `codeparse/langs/typescript.py`, `javascript.py` — `_enclosing_function`
  (Fix 1) only.
- `tests/chunkshop/codeparse/test_corpus_invariants.py` — new, self-contained,
  extra-gated.
- `tests/fixtures/codeparse/<lang>/realistic.<ext>` — new fixtures.

No public API changes. `ParseResult` / `Symbol` / `CallSite` shapes unchanged.

## Testing

- Per-language unit test for Fix 1 (nested-function call rolls up to outermost
  emitted symbol).
- Python span tests for Fix 2 (decorator included; boundary `line_end`).
- Corpus invariant test (Fix 4) — the regression net.
- Full `tests/chunkshop/codeparse/` + `test_code_relationships_extractor.py` +
  `test_symbol_aware_chunker.py` stay green.

## Risks / open questions

- **TS/JS arrow functions & function expressions** are not `function_declaration`,
  so a call inside an anonymous arrow walks up to the nearest *named* enclosing
  function — already correct, and the corpus test will confirm no orphan arises.
- **Decorator span change is a behavior change**: a decorated function's
  `original_content` (and therefore `start_line` metadata) grows to include the
  decorator lines. Any test asserting the old `start_line` must be updated. This
  is desired (decorators are part of the symbol) and will be called out in the
  changelog.
- Java local/anonymous classes are not covered by Fix 1; the corpus test will
  flag them if they produce orphans, in which case Java gets the same treatment.

## Out of scope (logged for follow-up)

- Sub-project **A**: Rust / C / C++ / C# / Ruby extractors.
- Sub-project **C**: import-aware cross-file resolution (Python read of #42).
- Emitting nested functions as first-class symbols.
