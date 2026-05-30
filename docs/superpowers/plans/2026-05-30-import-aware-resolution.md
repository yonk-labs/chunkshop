# Import-Aware Cross-File Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans / subagent-driven-development. Steps use `- [ ]`.

**Goal:** Make `CodeRelationshipsExtractor`'s ambiguous cross-file resolution import-aware: when multiple candidates share a callee's name, narrow to the one the caller file imports and emit a single precise edge (`resolution="import_resolved"`) instead of an N-way fan-out.

**Architecture:** Capture per-file import tokens in `extract()`; in `finalize()`'s ambiguous branches, filter candidates by import support. Single file, no schema change, `provenance` stays `"heuristic"`.

**Tech Stack:** Python 3.12, pytest. Tests use `parse_text` (needs `[code]` extra; gate with importorskip where parsing is involved — but most tests drive the extractor directly with python source, which needs `tree_sitter_python`).

**Spec:** `docs/superpowers/specs/2026-05-30-import-aware-resolution-design.md`.

**Run:** `cd python && uv run --no-sync pytest …`.

---

### Task 1: `_import_tokens` helper + capture per-file imports

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py` (`__init__`, `extract`, new module-level `_import_tokens`)
- Test: `python/tests/chunkshop/test_code_relationships_import_aware.py` (create)

- [ ] **Step 1:** Write failing unit tests for `_import_tokens`:

```python
from chunkshop.extractors.code_relationships import _import_tokens

def test_import_tokens_python():
    toks = _import_tokens(["from foo.bar import helper", "import os"])
    assert {"foo", "bar", "helper", "os"} <= toks

def test_import_tokens_rust_and_c():
    assert "calc" in _import_tokens(['#include "calc.h"'])
    assert {"crate", "a", "b"} <= _import_tokens(["use crate::a::b;"])

def test_import_tokens_empty():
    assert _import_tokens([]) == set()
```

- [ ] **Step 2:** Run → fail (`_import_tokens` undefined).
- [ ] **Step 3:** Add module-level helper near `_key` (~line 170):

```python
import re as _re

_IDENT_SPLIT = _re.compile(r"[^A-Za-z0-9_]+")


def _import_tokens(imports: list[str]) -> set[str]:
    """Lowercased identifier tokens across all of a file's import strings.

    Language-agnostic: splits each import on non-identifier chars so
    ``"from foo.bar import helper"`` -> {"from","foo","bar","import","helper"}
    and ``"use crate::a::b;"`` -> {"use","crate","a","b"}. Keyword noise is
    harmless — narrowing matches against a candidate file's *stem*, never a
    keyword.
    """
    out: set[str] = set()
    for imp in imports:
        for tok in _IDENT_SPLIT.split(imp):
            if tok:
                out.add(tok.lower())
    return out
```

- [ ] **Step 4:** Add `self._file_imports: dict[str, set[str]] = {}` in `__init__` (next to `self._symbols`).
- [ ] **Step 5:** In `extract()`, right after `result = parse_text(...)`, add:

```python
        # C: stash the caller file's import tokens for import-aware
        # cross-file resolution in finalize(). Merge (a file may arrive in
        # multiple chunks) so tokens accumulate rather than overwrite.
        if result.imports:
            self._file_imports.setdefault(path, set()).update(
                _import_tokens(result.imports)
            )
```

- [ ] **Step 6:** Run the `_import_tokens` tests → pass. Run `test_code_relationships_extractor.py` → still green.
- [ ] **Step 7:** Commit: `feat(code_relationships): capture per-file import tokens (C step 1)`.

---

### Task 2: `_import_supported` + narrow ambiguous CALLS

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py` (`finalize` CALLS ambiguous branch; new method `_import_supported`)
- Test: `python/tests/chunkshop/test_code_relationships_import_aware.py`

- [ ] **Step 1:** Write failing integration tests (append):

```python
from chunkshop.config import CodeRelationshipsExtractor as Cfg
from chunkshop.extractors.code_relationships import CodeRelationshipsExtractor

_DEF_A = "def helper(v):\n    return v * 2\n"
_DEF_B = "def helper(v):\n    return v\n"

def _caller(import_line: str) -> str:
    return f"{import_line}\n\ndef run(v):\n    return helper(v)\n"

def test_ambiguous_narrows_to_imported_candidate():
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_DEF_A, language="python", source_path="a.py")
    ext.extract(_DEF_B, language="python", source_path="b.py")
    ext.extract(_caller("from a import helper"), language="python", source_path="c.py")
    edges = ext.finalize(project_id="t")
    helper_edges = [e for e in edges if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")]
    assert len(helper_edges) == 1
    assert helper_edges[0]["dst_fqn"] == "a.helper"
    assert helper_edges[0]["evidence"]["resolution"] == "import_resolved"
    assert helper_edges[0]["provenance"] == "heuristic"

def test_no_import_keeps_ambiguous_fanout():
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_DEF_A, language="python", source_path="a.py")
    ext.extract(_DEF_B, language="python", source_path="b.py")
    ext.extract(_caller("# no import"), language="python", source_path="c.py")
    edges = ext.finalize(project_id="t")
    helper_edges = [e for e in edges if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")]
    assert len(helper_edges) == 2
    assert all(e["evidence"]["resolution"] == "ambiguous_name" for e in helper_edges)

def test_two_supported_keeps_fanout():
    ext = CodeRelationshipsExtractor(Cfg(type="code_relationships"))
    ext.extract(_DEF_A, language="python", source_path="a.py")
    ext.extract(_DEF_B, language="python", source_path="b.py")
    ext.extract(_caller("from a import helper\nfrom b import helper as h2"),
                language="python", source_path="c.py")
    edges = ext.finalize(project_id="t")
    helper_edges = [e for e in edges if e["edge_type"] == "CALLS" and e["dst_fqn"].endswith(".helper")]
    assert len(helper_edges) == 2
```

- [ ] **Step 2:** Run → `test_ambiguous_narrows_to_imported_candidate` FAILS (2 edges, resolution `ambiguous_name`); the other two PASS (current behavior).
- [ ] **Step 3:** Add the method (near the other helpers at the bottom of the class):

```python
    def _import_supported(self, caller_path: str, candidate_fqn: str) -> bool:
        """True if the caller file imports the module defining candidate_fqn.

        Conservative, language-agnostic: the candidate's file *stem*
        (``foo/bar.py`` -> ``bar``) must appear in the caller's import
        tokens. Two files defining the same name differ by stem, so the
        stem is the disambiguator.
        """
        tokens = self._file_imports.get(caller_path, set())
        if not tokens:
            return False
        cand = self._symbols.get(candidate_fqn)
        if cand is None:
            return False
        stem = Path(cand["file_path"]).stem.lower()
        return bool(stem) and stem in tokens
```

Ensure `from pathlib import Path` is imported at module top (add if absent).

- [ ] **Step 4:** In `finalize()`, replace the CALLS `else:` (ambiguous fan-out) block so it first tries import narrowing:

```python
            else:
                supported = [
                    c for c in candidates
                    if self._import_supported(pc["caller_path"], c)
                ]
                if len(supported) == 1:
                    _emit(
                        edge_type="CALLS",
                        src_fqn=pc["caller_fqn"],
                        dst_fqn=supported[0],
                        confidence=self.cfg.unique_match_confidence,
                        evidence={
                            "line": pc["line"],
                            "snippet": pc["snippet"],
                            "resolution": "import_resolved",
                            "candidates": candidates,
                        },
                        provenance="heuristic",
                    )
                else:
                    for cand in candidates:
                        _emit(
                            edge_type="CALLS",
                            src_fqn=pc["caller_fqn"],
                            dst_fqn=cand,
                            confidence=self.cfg.ambiguous_match_confidence,
                            evidence={
                                "line": pc["line"],
                                "snippet": pc["snippet"],
                                "resolution": "ambiguous_name",
                                "candidates": candidates,
                            },
                            provenance="heuristic",
                        )
```

- [ ] **Step 5:** Run the import-aware tests → all pass. Run `test_code_relationships_extractor.py` → green.
- [ ] **Step 6:** Commit: `feat(code_relationships): import-aware narrowing for ambiguous CALLS (C step 2)`.

---

### Task 3: Narrow ambiguous INHERITS/IMPLEMENTS + changelog + finish

**Files:**
- Modify: `python/src/chunkshop/extractors/code_relationships.py` (class-edge ambiguous branch)
- Test: `python/tests/chunkshop/test_code_relationships_import_aware.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1:** Confirm the `_pending_class_edges` entries carry the source file path. Inspect `_scan_class_edges`; the entry key is `src_path` or `src_file_path`. Use the actual key in the narrowing (read the code first). Write a failing test mirroring the CALLS narrowing but for a subclass whose base name is ambiguous across two files, with the caller file importing one.
- [ ] **Step 2:** Run → fails (fan-out).
- [ ] **Step 3:** Apply the same `supported = [...]; if len(supported) == 1: import_resolved else: fan-out` pattern to the class-edge `else:` branch, using the class entry's source-path key.
- [ ] **Step 4:** Run class-edge test + full extractor module → green.
- [ ] **Step 5:** CHANGELOG `### Added`: import-aware ambiguous resolution, `resolution="import_resolved"`, provenance unchanged.
- [ ] **Step 6:** `uv run --no-sync pytest -q` after `dev-setup.sh` → green.
- [ ] **Step 7:** Commit `docs(changelog): import-aware cross-file resolution (C)`; finish branch (PR) via finishing-a-development-branch.

---

## Self-Review

**Spec coverage:** Task 1 = capture imports + tokens; Task 2 = narrow CALLS; Task 3 = narrow class edges + docs. All spec sections covered. ✅
**Placeholders:** Task 3 Step 1 intentionally says "read the code first" for the class-edge path key — the only deferred lookup, because `_scan_class_edges`'s entry shape must be confirmed against source (it stores `src_fqn`/`src_path`); not a placeholder for *behavior*, which is fully specified. ✅
**Consistency:** `_import_tokens`, `_import_supported`, `_file_imports`, `resolution="import_resolved"` names are consistent across tasks and match the spec. `provenance` stays `"heuristic"` everywhere. ✅
