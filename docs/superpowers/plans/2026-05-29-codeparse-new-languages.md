# Codeparse New Languages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add real tree-sitter codeparse extractors for Rust, C, C++, C#, and Ruby (Python path), wired into the dispatch/fallback/chunker/pyproject seams, each validated with the orphan-edge + span invariants from sub-project B.

**Architecture:** Each `langs/<name>.py` mirrors the verified `langs/go.py` skeleton (`parse` → `_walk_symbols` + `_extract_call_sites` + `_enclosing_function` + `_extract_imports`). `_enclosing_function` MUST return the **outermost** emitted function (B's rule) to avoid orphan edges. Six additive wiring edits per language. Grammar node-types are pinned in the spec (probed from each grammar).

**Tech Stack:** Python 3.12, tree-sitter grammars (`tree-sitter-rust|c|cpp|c-sharp|ruby`), pytest.

**Spec:** `docs/superpowers/specs/2026-05-29-codeparse-new-languages-design.md`.

**Reference template:** `python/src/chunkshop/codeparse/langs/go.py` (method-via-receiver shape ≈ Rust method-via-impl; copy structure, swap node types).

**Run tests:** `cd python && uv run --no-sync pytest …` (`--no-sync` preserves the `[code]` extra; the 5 new grammars are added to it in Task 1).

---

### Task 1: Add grammar deps + shared wiring placeholders

**Files:**
- Modify: `python/pyproject.toml` (`[code]` extra)
- Modify: `python/src/chunkshop/codeparse/tree_sitter_wrapper.py` (`_SUPPORTED_LANGUAGES`, `_dispatch`)
- Modify: `python/src/chunkshop/codeparse/langs/regex_fallback.py` (`_EXT_TO_LANG`, `_REGEX_PATTERNS`)
- Modify: `python/src/chunkshop/chunkers/symbol_aware.py` (`_LANG_TO_EXT`)

- [ ] **Step 1:** Add to `pyproject.toml` `[code]`: `tree-sitter-rust>=0.23`, `tree-sitter-c>=0.23`, `tree-sitter-cpp>=0.23`, `tree-sitter-c-sharp>=0.23`, `tree-sitter-ruby>=0.23`.
- [ ] **Step 2:** Extend `_SUPPORTED_LANGUAGES` with `"rust", "c", "cpp", "csharp", "ruby"`.
- [ ] **Step 3:** Add a `_dispatch` branch per language importing `langs.<name>` (same shape as the `go` branch).
- [ ] **Step 4:** Extend `_EXT_TO_LANG`: `.rs→rust`, `.c/.h→c`, `.cc/.cpp/.cxx/.hpp/.hh→cpp`, `.cs→csharp`, `.rb→ruby`.
- [ ] **Step 5:** Extend `symbol_aware._LANG_TO_EXT`: `rust→.rs`, `c→.c`, `cpp→.cpp`, `csharp→.cs`, `ruby→.rb`.
- [ ] **Step 6:** Add best-effort `_REGEX_PATTERNS` entries for the 5 langs (class/struct + function/method anchors), so a `[code]`-absent env still yields symbols.
- [ ] **Step 7:** `uv sync --extra dev --extra code` then commit: `feat(codeparse): wire rust/c/cpp/csharp/ruby grammar deps + dispatch seams`.

> The per-language `langs/<name>.py` files don't exist yet, so the `_dispatch`
> branches will `ImportError` at runtime and fall through to regex until each
> language's Task lands — keep the branches but expect regex fallback until then.
> (Alternatively, add each `_dispatch` branch in that language's own task to keep
> the tree green per slice — preferred for vertical slicing.)

---

### Task 2: Rust extractor

**Files:** Create `langs/rust.py`; Create fixtures `tests/fixtures/codeparse/rust/sample.rs`; Create `tests/chunkshop/codeparse/test_parse_rust.py`.

Node-type mapping (from spec): `function_item`→function (or method inside
`impl_item`/`trait_item`); `impl_item`→use its `type` child as method
`parent_name`; `trait_item`→interface; `struct_item`/`enum_item`→class;
`call_expression`→callee `identifier`/`scoped_identifier`/`field_expression`.

- [ ] **Step 1:** Write `test_parse_rust.py` (failing) asserting: free `fn`, struct, `impl` method with `parent_name`==type, trait→interface, a nested-fn call attributes to the outermost emitted fn (no orphan). Use `parse_file` against the fixture.
- [ ] **Step 2:** Run → fail (regex fallback yields wrong/empty symbols).
- [ ] **Step 3:** Implement `langs/rust.py` from the `go.py` template with the mapping above; `_enclosing_function` returns the OUTERMOST `function_item`, parent = nearest `impl_item`/`trait_item` type.
- [ ] **Step 4:** Run `test_parse_rust.py` → pass.
- [ ] **Step 5:** Commit: `feat(codeparse): rust tree-sitter extractor`.

---

### Task 3: C extractor

**Files:** Create `langs/c.py`; fixture `c/sample.c`; `test_parse_c.py`.

Mapping: `function_definition`→walk `function_declarator`→name `identifier`;
`struct_specifier`→class; no methods; `call_expression`→`identifier`.

- [ ] Steps mirror Task 2 (write failing test → implement → pass → commit
  `feat(codeparse): c tree-sitter extractor`). `_enclosing_function`: outermost
  `function_definition`, parent always None (C has no methods).

---

### Task 4: C++ extractor

**Files:** Create `langs/cpp.py`; fixture `cpp/sample.cpp`; `test_parse_cpp.py`.

Mapping: `function_definition`+`function_declarator`; `class_specifier`/
`struct_specifier`→class; inline methods = `function_definition` in
`field_declaration_list` (parent = enclosing class name); out-of-line
`Class::method` → parent from `qualified_identifier` qualifier;
`namespace_definition` provides FQN context; `call_expression`.

- [ ] Steps mirror Task 2 → commit `feat(codeparse): c++ tree-sitter extractor`.

---

### Task 5: C# extractor

**Files:** Create `langs/csharp.py`; fixture `csharp/Sample.cs`; `test_parse_csharp.py`.

Mapping: `class_declaration`→class; `interface_declaration`→interface;
`method_declaration`→method (parent = enclosing class);
`namespace_declaration` context; calls are **`invocation_expression`** (function
= `identifier` or `member_access_expression` name).

- [ ] Steps mirror Task 2 → commit `feat(codeparse): c# tree-sitter extractor`.

---

### Task 6: Ruby extractor

**Files:** Create `langs/ruby.py`; fixture `ruby/sample.rb`; `test_parse_ruby.py`.

Mapping: `method`→function (or method inside `class`/`module`); `class`→class;
`module`→class; calls = `call` nodes (`method` field) only (best-effort; skip
bare sends). `_enclosing_function`: outermost `method`, parent = nearest
`class`/`module`.

- [ ] Steps mirror Task 2 → commit `feat(codeparse): ruby tree-sitter extractor`.

---

### Task 7: Parametrized invariants + changelog + finish

**Files:** Create `tests/chunkshop/codeparse/test_new_langs_invariants.py`; Modify `CHANGELOG.md`.

- [ ] **Step 1:** Parametrized test over the 5 new fixtures asserting no orphan
  caller node_ids and in-bounds spans (reuse the B corpus-test assertions).
- [ ] **Step 2:** Run the whole `tests/chunkshop/codeparse` suite → green.
- [ ] **Step 3:** Run `uv run --no-sync pytest -q` (after `dev-setup.sh`) → green.
- [ ] **Step 4:** CHANGELOG `### Added`: 5 new languages, lazy-import, regex
  fallback retained.
- [ ] **Step 5:** Commit `docs(changelog): rust/c/c++/c#/ruby codeparse extractors`; finish branch via finishing-a-development-branch.

---

## Self-Review

**Spec coverage:** Task 1 = wiring + deps; Tasks 2–6 = one extractor per language
with the spec's verified node-types; Task 7 = invariants + docs. Every language
in the spec maps to a task. ✅
**Placeholder scan:** Node-type mappings are concrete (probed, in spec); the
code template is `go.py` (verified, in-repo). No TBD. ✅
**Correctness rule:** Every task's `_enclosing_function` step states the
outermost-emitted-function rule (B's fix) — prevents reintroducing orphan edges. ✅
