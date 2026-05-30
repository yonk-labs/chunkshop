# Codeparse New Languages — Design Spec (Sub-project A)

**Date:** 2026-05-29
**Branch / worktree:** `feat/codeparse-new-langs` (`../chunkshop-codeparse-langs`, off `main`)
**Status:** approved-by-autonomy (user directed "continue autonomously", B→A→C sequencing)
**Depends on:** sub-project B (merged to `main` `5dc8854`) — the hardened
outermost-enclosing-function rule and the corpus invariant test are the safety
net this sub-project relies on.

## Context

chunkshop's `codeparse/` layer has real tree-sitter extractors for 5 languages
(Python, Java, Go, TypeScript, JavaScript). This sub-project adds **Rust, C,
C++, C#, and Ruby** on the Python path. Adding a language is a known recipe —
one `langs/<name>.py` plus six small wiring edits — but each grammar has its own
node-type vocabulary, so each extractor is bespoke. The grammar packages are
verified installable and `.language()`-compatible, and the node types below were
probed directly from each grammar (not guessed).

## The "add a language" recipe (per language)

1. **`langs/<name>.py`** — tree-sitter extractor exposing
   `parse(*, source, file_path, project_id="default") -> ParseResult`. Raises on
   import/parse error (the wrapper catches and falls back to regex).
2. **`tree_sitter_wrapper._dispatch`** — new `if language == "<name>"` branch.
3. **`tree_sitter_wrapper._SUPPORTED_LANGUAGES`** — add the tag.
4. **`regex_fallback._EXT_TO_LANG`** — add the file extension(s).
5. **`regex_fallback._REGEX_PATTERNS`** (+ optional `_IMPORT_REGEX`) — best-effort
   fallback so a `[code]`-absent environment still yields symbols.
6. **`symbol_aware._LANG_TO_EXT`** — add the representative extension.
7. **`pyproject.toml [code]`** — add the grammar dependency.
8. Fixtures (`tests/fixtures/codeparse/<name>/`) + parse tests + a per-language
   invariant test (no orphan callers, in-bounds spans) reusing B's pattern.

## Non-negotiable correctness rule (inherited from B)

Every extractor's `_enclosing_function` (or equivalent caller attribution) MUST
attribute a call to the **outermost emitted symbol**, never an inner nested
function — otherwise it reintroduces the orphan-edge bug B just fixed. The
corpus/invariant tests enforce this per language.

## Verified grammar node types (probed 2026-05-29)

**Rust** (`tree-sitter-rust`): `function_item` (free fn / method — methods are
`function_item` inside an `impl_item`/`trait_item` `declaration_list`),
`impl_item` (has a `type` child → the impl's type name = method `parent_name`),
`trait_item` (→ `interface`), `struct_item` / `enum_item` (→ `class`),
`call_expression` (callee `identifier`, `scoped_identifier`, or
`field_expression` field). Methods group under their `impl` type.

**C** (`tree-sitter-c`): `function_definition` whose `declarator` is a
`function_declarator` whose `declarator` is the name `identifier`;
`struct_specifier` (→ `class`); no methods; `call_expression` (callee
`identifier`).

**C++** (`tree-sitter-cpp`): `function_definition` + `function_declarator`;
`class_specifier` / `struct_specifier` (→ `class`) with methods as
`function_definition` inside the `field_declaration_list`;
`namespace_definition`; `call_expression`. Out-of-line `Class::method`
definitions (declarator is a `qualified_identifier`) resolve `parent_name` from
the qualifier when present. Scope is one level deep (class methods), matching
the other languages.

**C#** (`tree-sitter-c-sharp`): `class_declaration`, `interface_declaration`
(→ `interface`), `method_declaration` (methods inside a class),
`namespace_declaration`; calls are **`invocation_expression`** (NOT
`call_expression`) whose `function` is an `identifier` or
`member_access_expression` `name`.

**Ruby** (`tree-sitter-ruby`): `method` (def — `name` field), `class`, `module`
(→ `class`/`interface`-ish; we map `module` → `class`). Methods are `method`
inside a `class`/`module` body. Calls are `call` nodes (`method` field) plus
bare-identifier sends; Ruby call detection is **best-effort** (dynamic
language) — we capture `call` nodes' method names and skip the ambiguous bare
sends to avoid noise.

## Per-language scope decisions (YAGNI)

- **Rust:** methods via `impl`/`trait`; free fns; structs/enums as `class`,
  traits as `interface`. Macros, closures (`|x| ...`), and `mod` nesting are out
  of scope — closures are not emitted (one level deep), matching the contract.
- **C:** functions + structs only (C has no methods). Typedefs out of scope.
- **C++:** functions, classes/structs + their inline methods, namespaces as
  context for FQN. Templates parse fine (the grammar handles them); template
  *specialization* edge cases are best-effort. Operator overloads emitted by
  declarator name.
- **C#:** classes, interfaces, methods, namespaces; properties/fields out of
  scope. `invocation_expression` for calls.
- **Ruby:** `def`/`class`/`module`; `call`-node calls only. Blocks, metaprogramming,
  and bare sends out of scope.

## Components & boundaries

Each `langs/<name>.py` is self-contained and mirrors `go.py`'s shape
(`_walk_symbols`, `_extract_call_sites`, `_enclosing_function`,
`_extract_imports`, module-level `parse`). No shared-helper refactor — the
existing extractors don't share a base, and copying the ~50-line skeleton keeps
each language independently readable and testable (matches the established
pattern; DRY does not justify a premature framework here).

The six wiring edits are additive and identical in shape to the Go/TS/JS
addition in #40.

## Testing

Per language:
- A `tests/fixtures/codeparse/<name>/sample.<ext>` realistic fixture with a free
  function, a type with a method, the method calling the free function, and a
  **nested function/closure that makes a call** (the orphan-edge trigger).
- A `test_parse_<name>.py` asserting exact symbols (names, types, parents) and
  call sites.
- A per-language invariant assertion (no orphan callers; in-bounds spans),
  folded into a parametrized `test_new_langs_invariants.py`.

Plus: the existing corpus invariant test stays green (it only parses Python),
and the full suite stays green with the expanded `[code]` extra.

## Risks / open questions

- **Ruby call fidelity** is the weakest — dynamic dispatch means many "calls"
  are indistinguishable from local-variable reads at parse time. We deliberately
  under-capture (only `call` nodes) rather than over-capture; documented.
- **C++ out-of-line methods** (`void C::m() {}`) — `parent_name` comes from the
  `qualified_identifier` qualifier; nested namespaces may flatten. Best-effort,
  covered by a fixture.
- **`[code]` extra grows** by 5 wheels. The extractors stay import-lazy, so the
  base install is unaffected; only `[code]` users pull the new grammars.

## Out of scope (deferred)

- Sub-project **C**: import-aware cross-file resolution (Python read of #42).
- Rust-side ports of these extractors (RM-C family) — Python path only.
- regex-fallback parity with tree-sitter fidelity (fallback stays "best common
  case").
