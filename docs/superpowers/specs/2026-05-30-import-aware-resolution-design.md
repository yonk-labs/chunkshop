# Import-Aware Cross-File Resolution — Design Spec (Sub-project C)

**Date:** 2026-05-30
**Branch / worktree:** `feat/import-aware-resolution` (`../chunkshop-import-resolution`, off `main` `5080b36`)
**Status:** approved-by-autonomy (user: "do c" after B+A merged)
**Relation to #42:** the realistic **Python-path** read of issue #42 (true
`tree-sitter-stack-graphs` is Rust-only, hence the brief deferred SCIP-grade
resolution to Rust). This sub-project does NOT implement stack-graphs; it makes
the existing name-heuristic resolver *import-aware* so many ambiguous guesses
become precise.

## Context

`CodeRelationshipsExtractor.finalize()` resolves cross-file `CALLS` /
`INHERITS` / `IMPLEMENTS` edges by bare-name matching against `self._by_name`
(name → set of candidate FQNs):

- **1 candidate** → `resolution="unique_name"`, one edge.
- **N candidates** → `resolution="ambiguous_name"`, **one edge per candidate**
  (fan-out). Every cross-file edge is tagged `provenance="heuristic"`.

The fan-out is the imprecision: if two files each define `helper`, a call to
`helper` emits edges to *both*, even when the caller's file imports only one of
them. The caller file's imports are already parsed (`ParseResult.imports`) — but
`extract()` **discards them today**. This sub-project captures them and uses
them to narrow the ambiguous set.

## Goal / Non-goals

**Goal:** In the ambiguous (`N > 1`) branch, prefer candidates whose defining
module/file the caller actually imports. When exactly one candidate is
import-supported, emit a single precise edge (`resolution="import_resolved"`,
unique-match confidence) instead of the N-way fan-out.

**Non-goals**
- Stack-graphs / SCIP-grade scope resolution (Rust, deferred).
- Changing the `unique_name` or `intra_file` paths (already precise).
- Schema / `edge_kind` change. `provenance` stays `"heuristic"` — an
  import-narrowed edge is a *stronger* heuristic, not AST/SCIP truth; the new
  `resolution` tag is what distinguishes it. (Promoting it to a new provenance
  value is out of scope — that's a schema/contract decision for CS-3/SCIP.)
- Per-language import *semantics* (resolving `use crate::a::b` to a real path).
  We use a deliberately simple, language-agnostic token-overlap heuristic.

## Design

### 1. Capture per-file imports

Add `self._file_imports: dict[str, set[str]] = {}` to `__init__`. In
`extract()`, after parsing, store the caller file's import *tokens*:

```python
self._file_imports[path] = _import_tokens(result.imports)
```

`_import_tokens(imports: list[str]) -> set[str]` splits every import string on
non-identifier characters and lowercases — e.g. `"from foo.bar import helper"` →
`{"from", "foo", "bar", "import", "helper"}`, `"use crate::a::b;"` →
`{"use", "crate", "a", "b"}`, `#include "calc.h"` → `{"include", "calc", "h"}`.
Keyword noise (`from`/`import`/`use`/`include`) is harmless: it only ever
*adds* tokens, and matching is against a candidate's file *stem*, which is never
a language keyword.

### 2. Narrow ambiguous candidates by import support

A new helper:

```python
def _import_supported(self, caller_path, candidate_fqn) -> bool:
    tokens = self._file_imports.get(caller_path, set())
    if not tokens:
        return False
    cand = self._symbols.get(candidate_fqn)
    if cand is None:
        return False
    stem = Path(cand["file_path"]).stem.lower()   # "foo/bar.py" -> "bar"
    return bool(stem) and stem in tokens
```

In `finalize()`'s ambiguous CALLS branch (and the identical INHERITS/IMPLEMENTS
branch), before fanning out:

```python
supported = [c for c in candidates if self._import_supported(pc["caller_path"], c)]
if len(supported) == 1:
    _emit(..., dst_fqn=supported[0],
          confidence=self.cfg.unique_match_confidence,
          evidence={..., "resolution": "import_resolved", "candidates": candidates},
          provenance="heuristic")
    continue
# else: unchanged N-way ambiguous fan-out
```

The class-edge branch carries `src_path` (the `_pending_class_edges` entry's
file path) for the same narrowing.

### Why stem-in-tokens (and its limits)

Two files both defining `helper` differ by their *file stem* (`a.py` vs
`b.py`); only the imported file's stem appears in the caller's import tokens, so
the stem is the disambiguator. This is intentionally conservative:

- **0 supported** → keep the ambiguous fan-out (no regression; we never *drop*
  edges, only collapse a fan-out when import evidence is decisive).
- **≥2 supported** → keep the ambiguous fan-out (import evidence didn't decide).
- Re-exports, aliased imports (`import foo.bar as fb`), and wildcard imports may
  not narrow — acceptable; they fall back to today's behavior.

## Components & boundaries

- `extractors/code_relationships.py` — `__init__` (one dict), `extract()` (one
  store line), `finalize()` (narrow in two branches), two module-level/private
  helpers (`_import_tokens`, `_import_supported`). ~40 lines, one file.
- No change to `codeparse/`, the sink, the CLI, or any schema.

## Testing

- `_import_tokens` unit tests (python / rust / c import string shapes).
- Ambiguous-then-narrowed: two files define `helper`; a third imports one of
  them and calls `helper` → exactly one edge, `resolution="import_resolved"`.
- No-import-evidence: same setup without the import → unchanged 2-edge fan-out
  (`resolution="ambiguous_name"`), proving no regression.
- Two-supported: caller imports both → fan-out preserved.
- Existing `test_code_relationships_extractor.py` stays green (the SC-004
  provenance tests still pass — `import_resolved` edges are still `heuristic`).
- Live-PG round-trip optional (DB-gated) — `import_resolved` rows persist like
  any other `heuristic` edge (no schema change).

## Risks / open questions

- **Stem collisions:** two candidate files with the same stem in different dirs
  (`a/util.py`, `b/util.py`) both match a `util` import token → stays ambiguous
  (≥2 supported). Safe (no wrong narrowing), just no improvement.
- **`provenance` stays `heuristic`:** if a future consumer wants to rank
  import-resolved above bare-name, it keys on `evidence.resolution`, not
  `provenance`. Documented in the changelog.

## Out of scope (deferred)

- SCIP / stack-graphs resolver (Rust, #42 proper).
- A dedicated `provenance` value for import-resolved edges (schema/contract
  decision).
- Resolving import *aliases* and re-exports to true target modules.
