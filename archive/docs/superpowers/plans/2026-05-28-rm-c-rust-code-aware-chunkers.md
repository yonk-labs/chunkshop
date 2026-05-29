# RM-C Rust Code-Aware + Symbol-Aware Chunkers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Python's `code_aware` + `symbol_aware` chunkers to Rust so chunkshop-rs and chunkshop-py emit byte-equivalent code-symbol chunks into the same pgvector tables.

**Architecture:** Three layers — (1) foundational primitives (`build_fqn`, `code_symbol_node_id`) ported verbatim with TDD parity; (2) per-language symbol extraction via `tree-sitter` + per-grammar crates, mirroring Python's `Query` / `QueryCursor` pattern; (3) `SymbolAwareChunker` wired into `ChunkerConfig` behind opt-in Cargo features. Cross-port byte-equivalence is enforced by a hybrid harness: Rust `proptest` for breadth + Python pytest invoking a thin `chunkshop-fqn-cli` Rust binary for curated cross-port vectors.

**Tech Stack:** Rust 2021 edition, `tree-sitter` 0.26.x, `tree-sitter-python` + `tree-sitter-java` + `tree-sitter-tags` (all MIT, first-party), `sha1` crate (MIT/Apache-2.0), `proptest` (dev-dep), Python 3.12+ pytest. No new system deps (`cc` is already required by `fastembed`/`ort`).

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rm-c-rust-code-aware-chunkers.md` — re-read at every DC-XXX checkpoint.

**Pre-requisite:** PR #39 (`fix(codeparse): normalize path separators in build_fqn for cross-platform parity`) must merge before SC-003 cross-port property test is meaningful. SC-001/002/004/005 work CAN begin before #39 merges; just don't run the cross-port harness until the Python spec is stable.

---

## File Structure

**Created (Rust):**
- `rust/chunkshop/src/codeparse/mod.rs` — public module re-exports
- `rust/chunkshop/src/codeparse/fqn.rs` — `build_fqn` port + tests (always built, no feature gate — used by foundations)
- `rust/chunkshop/src/codeparse/id.rs` — `code_symbol_node_id` port + tests
- `rust/chunkshop/src/codeparse/symbol.rs` — `Symbol` struct mirroring Python's pydantic `Symbol`
- `rust/chunkshop/src/codeparse/langs/mod.rs` — language dispatcher
- `rust/chunkshop/src/codeparse/langs/python.rs` — Python tree-sitter Query-based extractor (gated `code-aware-python`)
- `rust/chunkshop/src/codeparse/langs/java.rs` — Java equivalent (gated `code-aware-java`)
- `rust/chunkshop/src/chunkers/symbol_aware.rs` — `SymbolAwareChunker` (gated `code-aware`)
- `rust/chunkshop/src/bin/fqn-cli.rs` — thin CLI for cross-port test harness
- `rust/chunkshop/tests/cross_port_proptest.rs` — `proptest` invariants

**Created (Python):**
- `python/tests/chunkshop/test_rust_cross_port_parity.py` — invokes `fqn-cli` Rust binary, asserts byte-equal output for 50+ curated vectors
- `python/tests/chunkshop/test_rm_c_e2e_parity.py` — E2E ingest via both ports, joins on `node_id`, asserts row-level metadata equality
- `python/tests/fixtures/rm-c-parity/` — fixture corpus (5-10 `.py` + 3-5 `.java` files)
- `docs/samples/rm-c-parity/rm-c-parity-py.yaml` — Python ingest config for fixtures
- `docs/samples/rm-c-parity/rm-c-parity-rs.yaml` — Rust ingest config for fixtures

**Modified (Rust):**
- `rust/chunkshop/Cargo.toml` — add `sha1`, `tree-sitter`, `tree-sitter-{python,java,tags}`, `proptest` (dev), `[features]` block, `[[bin]]` for `fqn-cli`
- `rust/chunkshop/src/lib.rs` — `pub mod codeparse;` (always) + `pub mod chunkers::symbol_aware;` (gated)
- `rust/chunkshop/src/config.rs` — add `SymbolAware(SymbolAwareChunkerConfig)` variant to `ChunkerConfig` enum at line 604 (gated `#[cfg(feature = "code-aware")]`)
- `rust/chunkshop/src/chunker.rs` — extend `build_chunker(cfg)` factory at line 1498 to dispatch `ChunkerConfig::SymbolAware` (gated)
- `rust/chunkshop/README.md` — new "Code-aware chunking" section with feature-flag matrix + binary-size delta table

**Worktree:** `git worktree add ../chunkshop-rm-c -b feat/rm-c` from `main` (after PR #39 merges, or before — see Task 1).

---

## Task 1: Worktree setup + dep prime

**Files:** none modified — environment setup only.

- [ ] **Step 1: Confirm PR #39 status**

Run: `gh pr view 39 --repo yonk-labs/chunkshop --json state,mergeable -q '{state: .state, mergeable: .mergeable}'`

If `state: MERGED`: branch from current `main` so Rust port has the OS-fixed `build_fqn` spec to mirror.
If `state: OPEN`: branch from current `main` anyway. Note that SC-003 cross-port harness (Tasks 6-9) must wait for #39 to merge — until then, the Python output is OS-dependent and Rust has no stable target.

- [ ] **Step 2: Create worktree**

```bash
cd /home/yonk/yonk-tools/chunkshop
git fetch origin
git worktree add ../chunkshop-rm-c -b feat/rm-c
cd ../chunkshop-rm-c
git status -sb   # confirms `## feat/rm-c`
```

- [ ] **Step 3: Prime Python venv (needed for cross-port tests later)**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
uv sync --extra dev --extra code --extra extractors
uv run --no-sync python -c "import chunkshop.codeparse; print('codeparse OK')"
```

Expected: prints `codeparse OK`. If it fails, the Python install is bad — investigate before continuing.

- [ ] **Step 4: Confirm Rust workspace builds clean from main**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo build --workspace 2>&1 | tail -5
```

Expected: `Finished ...`. Establishes a baseline. If cargo build fails on a freshly-branched main, that's a pre-existing problem to escalate before adding code.

No commit — this is setup.

---

## Task 2: ⛔ DC-001 — Spec-lock audit on Python `build_fqn` + `code_symbol_node_id`

**Purpose:** Drift Checkpoint DC-001 from the brief: re-read the Python source-of-truth on `main` HEAD so the port mirrors what's actually there, not the brief's description of it.

**Files:**
- Read: `python/src/chunkshop/codeparse/fqn.py`
- Read: `python/src/chunkshop/codeparse/id.py`
- Read: `python/tests/chunkshop/codeparse/test_fqn.py`
- Read: `python/tests/chunkshop/codeparse/test_id.py`
- Read: `skill-output/mission-brief/Mission-Brief-rm-c-rust-code-aware-chunkers.md`

- [ ] **Step 1: Re-read the mission brief**

Read it end-to-end. Confirm understanding of SC-001 through SC-006 + Out of Scope list.

- [ ] **Step 2: Read `fqn.py` and capture the EXACT algorithm**

```bash
cat /home/yonk/yonk-tools/chunkshop-rm-c/python/src/chunkshop/codeparse/fqn.py
```

Capture in a scratch note (not committed — your working memory):
- The separator-normalize logic (`replace("\\", "/")`)
- The empty-segment filter (`[p for p in ... if p]`)
- The 3-component window logic (`parts[-3:] if len(parts) >= 3 else parts`)
- The extension-strip regex (`re.sub(r"\.[^.]+$", "", path_prefix)`)
- The parent-name composition (`f"{path_prefix}.{parent_name}.{symbol_name}"`)

These five rules ARE the spec. The Rust port implements them verbatim.

- [ ] **Step 3: Read `id.py` and capture the SHA1 recipe**

```bash
cat /home/yonk/yonk-tools/chunkshop-rm-c/python/src/chunkshop/codeparse/id.py
```

Capture:
- Input concatenation: `f"{project_id}:{language}:{file_path}:{fqn}"` THEN `f"{project_id}:{fqn_full}"` (note the double wrap — see Python source)
- SHA1 hex digest
- Truncate to `[:16]` (first 16 hex chars)
- Prefix: `"node-"`

- [ ] **Step 4: Read both test files and enumerate every case**

```bash
cat /home/yonk/yonk-tools/chunkshop-rm-c/python/tests/chunkshop/codeparse/test_fqn.py
cat /home/yonk/yonk-tools/chunkshop-rm-c/python/tests/chunkshop/codeparse/test_id.py
```

You need EVERY test name + EVERY input/output pair. These become the Rust test mirror in Tasks 4 and 5. Count them — confirm 14 fqn cases (7 pre-existing + 7 cross-platform post-PR #39) and 6 id cases. If counts diverge, the brief's claims are stale — note and re-baseline.

- [ ] **Step 5: Self-check DC-001 pass**

Every Rust test you write in Tasks 4/5 must trace to a specific Python test name. If you can't pair them 1-to-1, the audit is incomplete.

No commit.

---

## Task 3: Cargo + codeparse module scaffold

**Files:**
- Modify: `rust/chunkshop/Cargo.toml`
- Create: `rust/chunkshop/src/codeparse/mod.rs`
- Modify: `rust/chunkshop/src/lib.rs`

- [ ] **Step 1: Add `sha1` to Cargo.toml dependencies**

Open `rust/chunkshop/Cargo.toml`, find the `[dependencies]` section, add:

```toml
sha1 = "0.10"
```

- [ ] **Step 2: Create the codeparse module skeleton**

Create `rust/chunkshop/src/codeparse/mod.rs` with:

```rust
//! Code-symbol extraction primitives ported from Python `chunkshop.codeparse`.
//!
//! Module structure mirrors Python:
//! - `fqn` — deterministic FQN builder, byte-equivalent to
//!   `chunkshop.codeparse.fqn.build_fqn`.
//! - `id` — deterministic node-id derivation, byte-equivalent to
//!   `chunkshop.codeparse.id.code_symbol_node_id`.
//! - `symbol` — `Symbol` struct mirroring Python's pydantic `Symbol`.
//! - `langs` — per-language tree-sitter extractors (feature-gated).
//!
//! Cross-port byte-equivalence is enforced by RM-C's hybrid test harness:
//! Rust `proptest` (this crate's `tests/cross_port_proptest.rs`) + Python
//! `pytest` (chunkshop-py's `tests/chunkshop/test_rust_cross_port_parity.py`)
//! invoking the `fqn-cli` binary.
//!
//! See `skill-output/mission-brief/Mission-Brief-rm-c-rust-code-aware-chunkers.md`.

pub mod fqn;
pub mod id;
pub mod symbol;

#[cfg(feature = "code-aware")]
pub mod langs;

pub use fqn::build_fqn;
pub use id::code_symbol_node_id;
pub use symbol::Symbol;
```

- [ ] **Step 3: Wire codeparse into lib.rs**

Edit `rust/chunkshop/src/lib.rs`, add `pub mod codeparse;` alongside the other `pub mod` declarations.

- [ ] **Step 4: Create stub files so the module compiles**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
touch chunkshop/src/codeparse/fqn.rs chunkshop/src/codeparse/id.rs chunkshop/src/codeparse/symbol.rs
```

Add minimal stub bodies that compile (will be replaced in Tasks 4/5/16):

`rust/chunkshop/src/codeparse/fqn.rs`:
```rust
//! Stub — filled in Task 4.
pub fn build_fqn(_file_path: &str, _symbol_name: &str, _parent_name: Option<&str>) -> String {
    String::new()
}
```

`rust/chunkshop/src/codeparse/id.rs`:
```rust
//! Stub — filled in Task 5.
pub fn code_symbol_node_id(
    _project_id: &str,
    _language: &str,
    _file_path: &str,
    _fqn: &str,
) -> String {
    String::new()
}
```

`rust/chunkshop/src/codeparse/symbol.rs`:
```rust
//! Stub — filled in Task 16.
#[derive(Debug, Clone)]
pub struct Symbol {
    pub name: String,
    pub fqn: String,
    pub symbol_type: String,
    pub line_start: u32,
    pub line_end: u32,
    pub parent_name: Option<String>,
}
```

- [ ] **Step 5: Build to confirm scaffold compiles**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo build --workspace 2>&1 | tail -5
```

Expected: `Finished ...`. If it fails, fix before continuing — likely missing module declaration.

- [ ] **Step 6: Commit**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/src/codeparse/ rust/chunkshop/src/lib.rs
git commit -m "feat(rm-c): scaffold codeparse module + sha1 dep

Adds rust/chunkshop/src/codeparse/{fqn,id,symbol}.rs as stubs wired through
mod.rs and lib.rs. sha1 = \"0.10\" added for code_symbol_node_id port.
Foundations filled in follow-up commits per the RM-C plan."
```

---

## Task 4: Port `build_fqn` (SC-001) — TDD

**Files:**
- Modify: `rust/chunkshop/src/codeparse/fqn.rs`

This is the foundation of cross-port equivalence. Every Python `test_fqn.py` case (post-PR #39: 14 total) gets a Rust mirror.

- [ ] **Step 1: Write the failing test module**

Replace `rust/chunkshop/src/codeparse/fqn.rs` body with:

```rust
//! Deterministic fully-qualified name builder.
//!
//! Byte-equivalent port of Python `chunkshop.codeparse.fqn.build_fqn`
//! (`python/src/chunkshop/codeparse/fqn.py` on `main` HEAD post-PR #39).
//!
//! Path separators are normalized cross-platform: both `/` and `\` are
//! treated as separators regardless of runtime OS, so the same logical
//! path produces the same FQN on Linux, macOS, and Windows.
//!
//! Cross-port parity tests live in:
//! - `rust/chunkshop/tests/cross_port_proptest.rs` (proptest invariants)
//! - `python/tests/chunkshop/test_rust_cross_port_parity.py` (curated vectors)

/// Compose a dotted fully-qualified name for `symbol_name`.
///
/// The FQN concatenates (a) the last 3 path components of `file_path`
/// with the file extension stripped, (b) `parent_name` if present
/// (the enclosing class for methods), and (c) `symbol_name`.
///
/// # Examples
///
/// ```
/// use chunkshop::codeparse::build_fqn;
/// assert_eq!(build_fqn("/a/b/c.py", "f", None), "a.b.c.f");
/// assert_eq!(build_fqn("c.py", "f", None), "c.f");
/// assert_eq!(build_fqn("/a/b/c.py", "g", Some("C")), "a.b.c.C.g");
/// ```
pub fn build_fqn(file_path: &str, symbol_name: &str, parent_name: Option<&str>) -> String {
    // Normalize separators so split is OS-independent. Filter empties to
    // absorb leading slashes ("/a/b/c.py") and consecutive separators
    // ("a//b/c.py"); mirrors Python's post-PR #39 behaviour at
    // python/src/chunkshop/codeparse/fqn.py:36-40.
    let normalized = file_path.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').filter(|p| !p.is_empty()).collect();

    let window: &[&str] = if parts.len() >= 3 {
        &parts[parts.len() - 3..]
    } else {
        &parts[..]
    };
    let raw_prefix = window.join(".");

    // Strip the file extension from the last segment only — same regex as
    // Python: r"\.[^.]+$"
    let path_prefix = strip_last_extension(&raw_prefix);

    match parent_name {
        Some(parent) => format!("{path_prefix}.{parent}.{symbol_name}"),
        None => format!("{path_prefix}.{symbol_name}"),
    }
}

fn strip_last_extension(s: &str) -> String {
    // Find the last `.` and check there's no `.` after it. Equivalent to
    // Python's re.sub(r"\.[^.]+$", "", s). Using string ops avoids a regex
    // dep for one cheap operation.
    if let Some(dot_idx) = s.rfind('.') {
        // Match Python: extension must contain at least one non-dot char
        let ext_chars = &s[dot_idx + 1..];
        if !ext_chars.is_empty() && !ext_chars.contains('.') {
            return s[..dot_idx].to_string();
        }
    }
    s.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- Mirror of python/tests/chunkshop/codeparse/test_fqn.py ---
    // Each test name + assertion pair is the Python equivalent.

    #[test]
    fn test_simple_function_in_short_path() {
        assert_eq!(build_fqn("c.py", "f", None), "c.f");
    }

    #[test]
    fn test_function_in_three_segment_path() {
        assert_eq!(build_fqn("a/b/c.py", "f", None), "a.b.c.f");
    }

    #[test]
    fn test_function_in_deep_path_keeps_only_last_three() {
        assert_eq!(
            build_fqn("/repo/src/pkg/mod/sub/file.py", "f", None),
            "mod.sub.file.f"
        );
    }

    #[test]
    fn test_method_with_parent_class() {
        assert_eq!(
            build_fqn("a/b/c.py", "method", Some("MyClass")),
            "a.b.c.MyClass.method"
        );
    }

    #[test]
    fn test_no_parent_when_explicit_none() {
        assert_eq!(build_fqn("a/b/c.py", "f", None), "a.b.c.f");
    }

    #[test]
    fn test_handles_extension_only_in_last_segment() {
        // "a.b" looks like file.ext but isn't the last segment — stays put.
        assert_eq!(build_fqn("repo/a.b/file.ts", "g", None), "repo.a.b.file.g");
    }

    #[test]
    fn test_distinct_inputs_produce_distinct_fqns() {
        use std::collections::HashSet;
        let fqns: HashSet<_> = [
            build_fqn("a/b/c.py", "f", None),
            build_fqn("a/b/c.py", "g", None),
            build_fqn("a/b/d.py", "f", None),
            build_fqn("a/b/c.py", "f", Some("C")),
        ]
        .into_iter()
        .collect();
        assert_eq!(fqns.len(), 4);
    }

    // --- Cross-platform path-separator equivalence (PR #39 regression suite) ---

    #[test]
    fn test_windows_and_posix_paths_produce_identical_fqn() {
        let posix = build_fqn("a/b/c.py", "f", None);
        let windows = build_fqn("a\\b\\c.py", "f", None);
        assert_eq!(posix, windows);
        assert_eq!(posix, "a.b.c.f");
    }

    #[test]
    fn test_mixed_separators_normalize_consistently() {
        assert_eq!(build_fqn("a/b\\c.py", "f", None), "a.b.c.f");
        assert_eq!(build_fqn("a\\b/c.py", "f", None), "a.b.c.f");
    }

    #[test]
    fn test_leading_separator_is_absorbed_posix() {
        assert_eq!(build_fqn("/a/b/c.py", "f", None), "a.b.c.f");
    }

    #[test]
    fn test_leading_separator_is_absorbed_windows() {
        assert_eq!(build_fqn("\\a\\b\\c.py", "f", None), "a.b.c.f");
    }

    #[test]
    fn test_consecutive_separators_collapse() {
        assert_eq!(build_fqn("a//b/c.py", "f", None), "a.b.c.f");
        assert_eq!(build_fqn("a\\\\b\\c.py", "f", None), "a.b.c.f");
    }

    #[test]
    fn test_deep_path_keeps_last_three_under_both_separators() {
        let posix = build_fqn("/repo/src/pkg/mod/sub/file.py", "f", None);
        let windows = build_fqn("C:\\repo\\src\\pkg\\mod\\sub\\file.py", "f", None);
        assert_eq!(posix, "mod.sub.file.f");
        assert_eq!(windows, "mod.sub.file.f");
    }

    #[test]
    fn test_method_fqn_invariant_across_separators() {
        let posix = build_fqn("a/b/c.py", "method", Some("MyClass"));
        let windows = build_fqn("a\\b\\c.py", "method", Some("MyClass"));
        assert_eq!(posix, windows);
        assert_eq!(posix, "a.b.c.MyClass.method");
    }
}
```

- [ ] **Step 2: Run tests to verify they pass (Python recipe was ported verbatim, so should be green on first try)**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo test --package chunkshop --lib codeparse::fqn 2>&1 | tail -20
```

Expected: `test result: ok. 14 passed`. If any fail: read the failure, compare your impl against Python source (`python/src/chunkshop/codeparse/fqn.py`), fix. The 5 algorithm rules from Task 2 Step 2 are the spec.

- [ ] **Step 3: Cross-check against Python's exact output for the same inputs**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
uv run --no-sync python -c "
from chunkshop.codeparse.fqn import build_fqn
cases = [
    ('c.py', 'f', None),
    ('a/b/c.py', 'f', None),
    ('/repo/src/pkg/mod/sub/file.py', 'f', None),
    ('a/b/c.py', 'method', 'MyClass'),
    ('repo/a.b/file.ts', 'g', None),
    ('a\\\\b\\\\c.py', 'f', None),  # Windows
    ('a/b\\\\c.py', 'f', None),       # mixed
    ('\\\\a\\\\b\\\\c.py', 'f', None),  # leading windows
    ('a//b/c.py', 'f', None),        # double posix
    ('C:\\\\repo\\\\src\\\\pkg\\\\mod\\\\sub\\\\file.py', 'f', None),  # deep windows
]
for fp, sym, par in cases:
    print(f'{fp!r:<55} {sym!r:<10} {par!r:<10} -> {build_fqn(fp, sym, par)!r}')
"
```

Compare the output to the Rust assertions in your test cases. If ANY pair disagrees, the Rust port has a bug — fix before continuing.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/codeparse/fqn.rs
git commit -m "feat(rm-c): port build_fqn to Rust with byte-equivalent output (SC-001)

Mirrors python/src/chunkshop/codeparse/fqn.py on main HEAD post-PR #39.
14 unit tests cover all Python test_fqn.py cases (7 pre-existing + 7
cross-platform). Algorithm rules preserved verbatim:
1. Normalize separators (\\\\ -> /)
2. Filter empty segments
3. Take last 3 components
4. Strip last extension
5. Optional parent.symbol composition

Cross-port harness (Rust proptest + Python pytest) follows in Tasks 6-8."
```

---

## Task 5: Port `code_symbol_node_id` (SC-002) — TDD

**Files:**
- Modify: `rust/chunkshop/src/codeparse/id.rs`

- [ ] **Step 1: Read Python `test_id.py` to enumerate cases**

```bash
cat /home/yonk/yonk-tools/chunkshop-rm-c/python/tests/chunkshop/codeparse/test_id.py
```

Confirm 6 test cases. Capture each test name + inputs + expected output (or at least the assertion shape — most assert "node-" prefix + 16-hex + determinism, not raw expected strings).

- [ ] **Step 2: Replace `rust/chunkshop/src/codeparse/id.rs` body**

```rust
//! Deterministic graph-node ID derivation for code symbols.
//!
//! Byte-equivalent port of Python `chunkshop.codeparse.id.code_symbol_node_id`
//! (`python/src/chunkshop/codeparse/id.py` on `main` HEAD).
//!
//! Composes `"node-" + sha1(project_id:language:file_path:fqn)[:16]`. The
//! 16-hex truncation gives 64 bits of collision resistance — plenty for any
//! single project, short enough to land in URLs and graph viewers.

use sha1::{Digest, Sha1};

/// Compose `"node-" + sha1(project_id:language:file_path:fqn)[:16]`.
///
/// Deterministic: same inputs always return the same ID. That property is
/// what makes the upsert path in a downstream sink (e.g. `ON CONFLICT (id)
/// DO UPDATE`) idempotent — re-running ingest against the same project
/// doesn't multiply rows.
pub fn code_symbol_node_id(
    project_id: &str,
    language: &str,
    file_path: &str,
    fqn: &str,
) -> String {
    // Mirror Python's double-wrap:
    //   fqn_full = f"{language}:{file_path}:{fqn}"
    //   digest = sha1(f"{project_id}:{fqn_full}").hexdigest()[:16]
    let fqn_full = format!("{language}:{file_path}:{fqn}");
    let to_hash = format!("{project_id}:{fqn_full}");

    let mut hasher = Sha1::new();
    hasher.update(to_hash.as_bytes());
    let digest_bytes = hasher.finalize();
    let hex = hex_encode(&digest_bytes);

    format!("node-{}", &hex[..16])
}

fn hex_encode(bytes: &[u8]) -> String {
    // Lowercase hex, matches Python hashlib.hexdigest() format.
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- Mirror of python/tests/chunkshop/codeparse/test_id.py ---

    #[test]
    fn test_deterministic_for_same_inputs() {
        let a = code_symbol_node_id("proj1", "python", "a/b/c.py", "a.b.c.f");
        let b = code_symbol_node_id("proj1", "python", "a/b/c.py", "a.b.c.f");
        assert_eq!(a, b);
    }

    #[test]
    fn test_id_format_is_node_prefix_plus_16_hex() {
        let id = code_symbol_node_id("proj1", "python", "a/b/c.py", "a.b.c.f");
        assert!(id.starts_with("node-"));
        let hex_part = &id[5..];
        assert_eq!(hex_part.len(), 16);
        assert!(
            hex_part.chars().all(|c| c.is_ascii_hexdigit() && (c.is_ascii_digit() || c.is_ascii_lowercase())),
            "expected lowercase hex chars only, got {hex_part}"
        );
    }

    #[test]
    fn test_different_projects_diverge() {
        let a = code_symbol_node_id("proj1", "python", "a/b/c.py", "a.b.c.f");
        let b = code_symbol_node_id("proj2", "python", "a/b/c.py", "a.b.c.f");
        assert_ne!(a, b);
    }

    #[test]
    fn test_different_languages_diverge() {
        let a = code_symbol_node_id("proj1", "python", "a/b/c.py", "a.b.c.f");
        let b = code_symbol_node_id("proj1", "java", "a/b/c.py", "a.b.c.f");
        assert_ne!(a, b);
    }

    #[test]
    fn test_different_files_diverge() {
        let a = code_symbol_node_id("proj1", "python", "a/b/c.py", "a.b.c.f");
        let b = code_symbol_node_id("proj1", "python", "a/b/d.py", "a.b.c.f");
        assert_ne!(a, b);
    }

    #[test]
    fn test_different_fqns_diverge() {
        let a = code_symbol_node_id("proj1", "python", "a/b/c.py", "a.b.c.f");
        let b = code_symbol_node_id("proj1", "python", "a/b/c.py", "a.b.c.g");
        assert_ne!(a, b);
    }
}
```

- [ ] **Step 3: Run tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo test --package chunkshop --lib codeparse::id 2>&1 | tail -15
```

Expected: `test result: ok. 6 passed`. If any fail, your SHA1 / format / truncation logic diverges from Python — fix.

- [ ] **Step 4: Cross-check raw hash against Python**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
uv run --no-sync python -c "
from chunkshop.codeparse.id import code_symbol_node_id
print(code_symbol_node_id('proj1', 'python', 'a/b/c.py', 'a.b.c.f'))
print(code_symbol_node_id('proj1', 'java', 'src/Main.java', 'Main.greet'))
"
```

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo run --quiet --example node_id_sanity 2>/dev/null || \
  cargo test --package chunkshop --lib codeparse::id -- --nocapture 2>&1 | grep "node-"
```

If no `examples/node_id_sanity.rs`, add a temporary `println!` to one of the tests, run with `--nocapture`, and compare the printed value to Python's output. They MUST match byte-for-byte. If they don't, the bug is in `to_hash` ordering, hex casing, or truncation.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/codeparse/id.rs
git commit -m "feat(rm-c): port code_symbol_node_id to Rust with byte-equivalent output (SC-002)

Mirrors python/src/chunkshop/codeparse/id.py. 6 unit tests cover all Python
test_id.py cases. Uses sha1 = \"0.10\" crate. Output format:
\"node-\" + sha1(project_id:language:file_path:fqn).hexdigest()[:16]

Cross-port hash equivalence verified manually against Python; automated via
the cross-port harness in Tasks 6-8."
```

---

## Task 6: `fqn-cli` Rust binary for Python cross-port harness (SC-003 part 1)

**Files:**
- Create: `rust/chunkshop/src/bin/fqn-cli.rs`
- Modify: `rust/chunkshop/Cargo.toml` (add `[[bin]]` entry)

This is a thin CLI that takes `(operation, args)` as command-line arguments and prints the result. Python's pytest invokes it as a subprocess for cross-port vectors.

- [ ] **Step 1: Create the binary**

`rust/chunkshop/src/bin/fqn-cli.rs`:

```rust
//! Thin CLI exposing `build_fqn` + `code_symbol_node_id` for cross-port
//! testing. Invoked by `python/tests/chunkshop/test_rust_cross_port_parity.py`.
//!
//! Usage:
//!   fqn-cli build-fqn <file_path> <symbol_name> [parent_name]
//!   fqn-cli node-id <project_id> <language> <file_path> <fqn>
//!
//! Stdout: the computed string, no trailing newline (use printf-style).
//! Stderr: error message + exit 1 on bad usage.

use chunkshop::codeparse::{build_fqn, code_symbol_node_id};
use std::process::exit;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: {} <build-fqn|node-id> <args...>", args[0]);
        exit(1);
    }
    match args[1].as_str() {
        "build-fqn" => match &args[2..] {
            [file_path, symbol_name] => {
                print!("{}", build_fqn(file_path, symbol_name, None));
            }
            [file_path, symbol_name, parent_name] => {
                let parent = if parent_name.is_empty() {
                    None
                } else {
                    Some(parent_name.as_str())
                };
                print!("{}", build_fqn(file_path, symbol_name, parent));
            }
            _ => {
                eprintln!("usage: build-fqn <file_path> <symbol_name> [parent_name]");
                exit(1);
            }
        },
        "node-id" => match &args[2..] {
            [project_id, language, file_path, fqn] => {
                print!(
                    "{}",
                    code_symbol_node_id(project_id, language, file_path, fqn)
                );
            }
            _ => {
                eprintln!("usage: node-id <project_id> <language> <file_path> <fqn>");
                exit(1);
            }
        },
        other => {
            eprintln!("unknown subcommand: {other}");
            exit(1);
        }
    }
}
```

- [ ] **Step 2: Add `[[bin]]` entry to Cargo.toml**

Edit `rust/chunkshop/Cargo.toml`, add at the end of the file (after `[features]` block or wherever existing `[[bin]]` entries live):

```toml
[[bin]]
name = "fqn-cli"
path = "src/bin/fqn-cli.rs"
```

- [ ] **Step 3: Build the binary**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo build --release --bin fqn-cli 2>&1 | tail -3
ls -la target/release/fqn-cli
```

Expected: binary exists at `target/release/fqn-cli`, ~few MB.

- [ ] **Step 4: Smoke-test the binary**

```bash
./target/release/fqn-cli build-fqn "a/b/c.py" "f"
echo
./target/release/fqn-cli build-fqn "a/b/c.py" "method" "MyClass"
echo
./target/release/fqn-cli node-id "proj1" "python" "a/b/c.py" "a.b.c.f"
echo
```

Expected:
```
a.b.c.f
a.b.c.MyClass.method
node-<16-hex-chars>
```

Compare to Python output for the same inputs (use the Step 4 cross-check command from Task 5).

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/bin/fqn-cli.rs rust/chunkshop/Cargo.toml
git commit -m "feat(rm-c): add fqn-cli binary for cross-port test harness (SC-003)

Thin CLI exposing build_fqn + code_symbol_node_id for Python pytest to
invoke as subprocess. Two subcommands: build-fqn and node-id. Output is
the raw result string, no trailing newline (subprocess.run().stdout.decode()
matches byte-for-byte against Python's function output). Used by the
Python-side cross-port harness in Task 8."
```

---

## Task 7: Rust `proptest` invariants (SC-003 part 2)

**Files:**
- Create: `rust/chunkshop/tests/cross_port_proptest.rs`
- Modify: `rust/chunkshop/Cargo.toml` (add `proptest` as dev-dep)

- [ ] **Step 1: Add `proptest` dev-dep**

Edit `rust/chunkshop/Cargo.toml`, in `[dev-dependencies]` section:

```toml
proptest = "1"
```

- [ ] **Step 2: Create the proptest file**

`rust/chunkshop/tests/cross_port_proptest.rs`:

```rust
//! Property-based invariants for build_fqn and code_symbol_node_id.
//!
//! These prove the Rust implementation's INTERNAL invariants (determinism,
//! separator normalization, output shape) over thousands of random inputs.
//! Cross-port BYTE equivalence with Python is enforced separately by
//! `python/tests/chunkshop/test_rust_cross_port_parity.py`.

use chunkshop::codeparse::{build_fqn, code_symbol_node_id};
use proptest::prelude::*;

// --- build_fqn invariants ---

proptest! {
    /// Same input always produces the same output.
    #[test]
    fn build_fqn_is_deterministic(
        file_path in "[a-zA-Z0-9/\\\\._]{1,80}",
        symbol_name in "[a-zA-Z_][a-zA-Z0-9_]{0,30}",
        parent in proptest::option::of("[a-zA-Z_][a-zA-Z0-9_]{0,30}"),
    ) {
        let parent_ref = parent.as_deref();
        let a = build_fqn(&file_path, &symbol_name, parent_ref);
        let b = build_fqn(&file_path, &symbol_name, parent_ref);
        prop_assert_eq!(a, b);
    }

    /// Backslash and forward slash are equivalent for the same logical path.
    #[test]
    fn build_fqn_normalizes_separators(
        components in proptest::collection::vec("[a-zA-Z_][a-zA-Z0-9_]{0,15}", 1..6),
        ext in "py|java|rs|go|ts|js",
        symbol_name in "[a-zA-Z_][a-zA-Z0-9_]{0,30}",
    ) {
        // Build a logical path two ways: posix-style and windows-style.
        let mut last = components.last().unwrap().clone();
        last.push('.');
        last.push_str(&ext);
        let mut posix_parts = components[..components.len() - 1].to_vec();
        posix_parts.push(last.clone());
        let posix_path = posix_parts.join("/");
        let windows_path = posix_parts.join("\\");

        let posix_fqn = build_fqn(&posix_path, &symbol_name, None);
        let windows_fqn = build_fqn(&windows_path, &symbol_name, None);
        prop_assert_eq!(posix_fqn, windows_fqn);
    }

    /// Output always ends with `.<symbol_name>` (and `.<parent>.<symbol_name>` if parent set).
    #[test]
    fn build_fqn_ends_with_symbol(
        file_path in "[a-zA-Z0-9/_]{1,40}\\.py",
        symbol_name in "[a-zA-Z_][a-zA-Z0-9_]{0,30}",
    ) {
        let out = build_fqn(&file_path, &symbol_name, None);
        prop_assert!(out.ends_with(&format!(".{symbol_name}")), "expected suffix .{symbol_name} in {out}");
    }
}

// --- code_symbol_node_id invariants ---

proptest! {
    /// Same input always produces the same ID.
    #[test]
    fn node_id_is_deterministic(
        project_id in "[a-zA-Z0-9_-]{1,20}",
        language in "python|java|go|rust|typescript|javascript",
        file_path in "[a-zA-Z0-9/_]{1,40}\\.(py|java|go|rs|ts|js)",
        fqn in "[a-zA-Z_][a-zA-Z0-9_.]{0,60}",
    ) {
        let a = code_symbol_node_id(&project_id, &language, &file_path, &fqn);
        let b = code_symbol_node_id(&project_id, &language, &file_path, &fqn);
        prop_assert_eq!(a, b);
    }

    /// Output is always "node-" + 16 lowercase hex chars.
    #[test]
    fn node_id_has_expected_shape(
        project_id in "[a-zA-Z0-9_-]{1,20}",
        language in "[a-z]+",
        file_path in "[a-zA-Z0-9/_]{1,40}",
        fqn in "[a-zA-Z_][a-zA-Z0-9_.]{0,60}",
    ) {
        let id = code_symbol_node_id(&project_id, &language, &file_path, &fqn);
        prop_assert!(id.starts_with("node-"));
        let hex = &id[5..];
        prop_assert_eq!(hex.len(), 16);
        prop_assert!(hex.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    }

    /// Different project IDs always diverge (collision = 1 in 2^64; safe to assert).
    #[test]
    fn node_id_diverges_for_different_projects(
        proj_a in "[a-z]{5,15}",
        proj_b in "[a-z]{5,15}",
        language in "python|java",
        file_path in "[a-zA-Z0-9/_]{1,30}\\.(py|java)",
        fqn in "[a-z_]+(\\.[a-z_]+){0,5}",
    ) {
        prop_assume!(proj_a != proj_b);
        let a = code_symbol_node_id(&proj_a, &language, &file_path, &fqn);
        let b = code_symbol_node_id(&proj_b, &language, &file_path, &fqn);
        prop_assert_ne!(a, b);
    }
}
```

- [ ] **Step 3: Run proptest**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo test --package chunkshop --test cross_port_proptest 2>&1 | tail -15
```

Expected: each test runs 256 cases by default → `test result: ok. 6 passed`. If any fails, proptest will print a minimized counterexample — that's a real bug, fix in `fqn.rs` or `id.rs`.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/tests/cross_port_proptest.rs
git commit -m "test(rm-c): add proptest invariants for build_fqn + code_symbol_node_id

6 proptest functions covering: determinism, separator normalization,
suffix invariant for build_fqn; determinism, output shape, and
project-id divergence for code_symbol_node_id. Each test runs 256 random
cases by default — together that's ~1500 cases per cargo test invocation.

Cross-port BYTE equivalence with Python is enforced separately by the
Python-side harness in Task 8."
```

---

## Task 8: Python cross-port pytest (SC-003 part 3)

**Files:**
- Create: `python/tests/chunkshop/test_rust_cross_port_parity.py`

- [ ] **Step 1: Create the Python test**

```python
"""Cross-port byte-equivalence tests for build_fqn + code_symbol_node_id.

Invokes the Rust ``fqn-cli`` binary as a subprocess and asserts that for a
curated 50+ vector set, the Rust output equals the Python output byte-for-
byte. This is the second half of RM-C's SC-003 cross-port harness; the Rust
proptest in ``rust/chunkshop/tests/cross_port_proptest.rs`` covers the
breadth side.

Skipped cleanly when the Rust binary isn't built. To build it::

    cd rust && cargo build --release --bin fqn-cli

Test PG / network not required.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from chunkshop.codeparse.fqn import build_fqn
from chunkshop.codeparse.id import code_symbol_node_id

REPO_ROOT = Path(__file__).resolve().parents[3]
FQN_CLI = REPO_ROOT / "rust" / "target" / "release" / "fqn-cli"

pytestmark = pytest.mark.skipif(
    not FQN_CLI.exists(),
    reason=f"Rust fqn-cli binary not built; run `cd rust && cargo build --release --bin fqn-cli`",
)


def _run_rust(*args: str) -> str:
    result = subprocess.run(
        [str(FQN_CLI), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fqn-cli failed: {result.stderr}")
    return result.stdout  # no trailing newline; Rust uses print! not println!


# --- 50+ curated vectors: drawn from test_fqn.py + test_id.py + adversarial extras ---

FQN_VECTORS: list[tuple[str, str, str | None]] = [
    # Mirror of test_fqn.py
    ("c.py", "f", None),
    ("a/b/c.py", "f", None),
    ("/repo/src/pkg/mod/sub/file.py", "f", None),
    ("a/b/c.py", "method", "MyClass"),
    ("a/b/c.py", "f", None),
    ("repo/a.b/file.ts", "g", None),
    # Cross-platform separators
    ("a\\b\\c.py", "f", None),
    ("a/b\\c.py", "f", None),
    ("a\\b/c.py", "f", None),
    ("/a/b/c.py", "f", None),
    ("\\a\\b\\c.py", "f", None),
    ("a//b/c.py", "f", None),
    ("a\\\\b\\c.py", "f", None),
    ("C:\\repo\\src\\pkg\\mod\\sub\\file.py", "f", None),
    ("a\\b\\c.py", "method", "MyClass"),
    # Adversarial: unicode, very long, dots
    ("repo/módulo/файл.py", "función", None),
    ("a/b/c.py", "你好", None),
    ("a/b/c.tar.gz", "f", None),  # only LAST extension stripped
    ("a/b/.hidden.py", "f", None),  # leading-dot filename
    ("a/b/c", "f", None),  # no extension
    ("", "f", None),  # empty path
    ("/" * 5 + "a/b/c.py", "f", None),  # many leading slashes
    ("a/b/c.py", "", None),  # empty symbol (unusual but documented)
    ("a/b/c.py", "f", ""),  # empty parent (falsy → no parent prefix)
    ("a/b/c.py", "f", "C.Nested"),  # parent with dot
    # Deep paths, mixed languages
    ("src/main/java/com/example/Foo.java", "bar", None),
    ("src/main/java/com/example/Foo.java", "method", "Foo"),
    ("internal/svc/handler.go", "ServeHTTP", None),
    ("packages/ui/src/components/Button.tsx", "render", None),
    ("packages/ui/src/components/Button.tsx", "default", None),
]

NODE_ID_VECTORS: list[tuple[str, str, str, str]] = [
    # (project_id, language, file_path, fqn)
    ("proj1", "python", "a/b/c.py", "a.b.c.f"),
    ("proj1", "python", "a/b/c.py", "a.b.c.f"),  # same input -> same id
    ("proj2", "python", "a/b/c.py", "a.b.c.f"),
    ("proj1", "java", "a/b/c.py", "a.b.c.f"),
    ("proj1", "python", "a/b/d.py", "a.b.c.f"),
    ("proj1", "python", "a/b/c.py", "a.b.c.g"),
    # Adversarial
    ("проект1", "python", "файл.py", "модуль.f"),
    ("p", "python", "/very/deep/nested/path/to/module.py", "to.module.function_name"),
    ("p", "java", "src/Main.java", "Main.greet"),
    ("p", "rust", "src/lib.rs", "lib.do_thing"),
    ("p", "typescript", "src/Foo.ts", "Foo.bar"),
    ("p", "javascript", "src/Foo.js", "Foo.bar"),
    ("project-with-dashes", "python", "a.py", "a.f"),
    ("project_with_underscores", "python", "a.py", "a.f"),
    ("p", "python", "a.py", ""),  # empty fqn (unusual but documented)
    ("p", "python", "", "a.b.c.f"),  # empty path
    ("p", "", "a.py", "a.f"),  # empty language
    ("", "python", "a.py", "a.f"),  # empty project_id
]

assert len(FQN_VECTORS) + len(NODE_ID_VECTORS) >= 48, "need 50+ vectors per brief"


@pytest.mark.parametrize("file_path,symbol_name,parent_name", FQN_VECTORS)
def test_build_fqn_cross_port_parity(file_path: str, symbol_name: str, parent_name: str | None) -> None:
    """Rust build_fqn output must equal Python build_fqn output byte-for-byte."""
    py_out = build_fqn(file_path, symbol_name, parent_name)
    rust_args = ["build-fqn", file_path, symbol_name]
    if parent_name is not None:
        rust_args.append(parent_name)
    rust_out = _run_rust(*rust_args)
    assert py_out == rust_out, (
        f"divergence for ({file_path!r}, {symbol_name!r}, {parent_name!r}): "
        f"python={py_out!r} rust={rust_out!r}"
    )


@pytest.mark.parametrize("project_id,language,file_path,fqn", NODE_ID_VECTORS)
def test_code_symbol_node_id_cross_port_parity(
    project_id: str, language: str, file_path: str, fqn: str
) -> None:
    """Rust code_symbol_node_id must equal Python code_symbol_node_id byte-for-byte."""
    py_out = code_symbol_node_id(project_id, language, file_path, fqn)
    rust_out = _run_rust("node-id", project_id, language, file_path, fqn)
    assert py_out == rust_out, (
        f"divergence for ({project_id!r}, {language!r}, {file_path!r}, {fqn!r}): "
        f"python={py_out!r} rust={rust_out!r}"
    )
```

- [ ] **Step 2: Build the Rust binary (prereq for running the test)**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo build --release --bin fqn-cli 2>&1 | tail -3
```

- [ ] **Step 3: Run the cross-port test in skip mode (verify skipif works)**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
mv ../rust/target/release/fqn-cli ../rust/target/release/fqn-cli.bak
uv run --no-sync pytest tests/chunkshop/test_rust_cross_port_parity.py -v 2>&1 | tail -10
mv ../rust/target/release/fqn-cli.bak ../rust/target/release/fqn-cli
```

Expected: all SKIPPED with the configured reason message.

- [ ] **Step 4: Run the cross-port test for real**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
uv run --no-sync pytest tests/chunkshop/test_rust_cross_port_parity.py -v 2>&1 | tail -25
```

Expected: all tests PASS. The exact pass count = `len(FQN_VECTORS) + len(NODE_ID_VECTORS)` (~48).

If any FAILS: read the assert message — it shows `python={...} rust={...}`. The divergence localizes the bug. Fix either `fqn.rs` / `id.rs` or the Python side (the latter is OUT OF SCOPE — Python is the spec). If Python and Rust diverge on an adversarial vector that nobody is using in practice, consider whether the vector is a real bug or a test-input mismatch (e.g., the Rust CLI argv encoding differs from Python's call).

- [ ] **Step 5: Commit**

```bash
git add python/tests/chunkshop/test_rust_cross_port_parity.py
git commit -m "test(rm-c): Python cross-port parity for build_fqn + code_symbol_node_id (SC-003)

Curated 48+ vector set covering test_fqn.py, test_id.py, and adversarial
extras (unicode, very long paths, multi-extension files, hidden files,
empty inputs). Each vector compares Python build_fqn/code_symbol_node_id
output against the Rust fqn-cli binary output byte-for-byte.

Skipped cleanly when fqn-cli binary isn't built. Runs in CI on every PR
touching either port."
```

---

## Task 9: ⛔ DC-002 — Foundation parity gate before chunker work

**Files:** none modified — pure audit.

**Purpose:** Drift Checkpoint DC-002 from the brief: foundation primitives MUST be byte-equivalent before any chunker work begins. If `build_fqn` or `code_symbol_node_id` is wrong, every chunk metadata field downstream is wrong too.

- [ ] **Step 1: Re-read the mission brief**

Confirm understanding of SC-001 / SC-002 / SC-003 satisfied state.

- [ ] **Step 2: Re-run Rust proptest with bumped case count**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
PROPTEST_CASES=2000 cargo test --package chunkshop --test cross_port_proptest 2>&1 | tail -10
```

Expected: all 6 tests pass with 2000 cases each = 12000 cases. If proptest finds a minimized counterexample at higher case counts, that's a real bug.

- [ ] **Step 3: Re-run Python cross-port pytest**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
uv run --no-sync pytest tests/chunkshop/test_rust_cross_port_parity.py -v 2>&1 | tail -10
```

Expected: all parametrized cases pass.

- [ ] **Step 4: Self-check**

If both Step 2 and Step 3 are green → DC-002 PASS. Foundation is byte-equivalent. Proceed to chunker work.

If either fails → STOP. Debug the divergence, fix it, re-run. Do NOT proceed to chunker work on a broken foundation.

No commit.

---

## Task 10: Cargo `[features]` matrix + module gates

**Files:**
- Modify: `rust/chunkshop/Cargo.toml`
- Modify: `rust/chunkshop/src/codeparse/mod.rs` (already added the `code-aware` gate in Task 3 — verify)

- [ ] **Step 1: Add the `[features]` block**

Edit `rust/chunkshop/Cargo.toml`. Add to (or create) the `[features]` block:

```toml
[features]
default = []

# Code-aware chunking (RM-C). Each grammar is opt-in to control binary size.
# See README.md "Code-aware chunking" for the size delta table.
code-aware = []  # Umbrella — implies all must-have grammars
code-aware-python = ["code-aware", "dep:tree-sitter", "dep:tree-sitter-python", "dep:tree-sitter-tags"]
code-aware-java   = ["code-aware", "dep:tree-sitter", "dep:tree-sitter-java",   "dep:tree-sitter-tags"]

# Should-have grammars (added in follow-up; placeholders for SC-006 docs).
code-aware-go         = ["code-aware", "dep:tree-sitter", "dep:tree-sitter-go",         "dep:tree-sitter-tags"]
code-aware-typescript = ["code-aware", "dep:tree-sitter", "dep:tree-sitter-typescript", "dep:tree-sitter-tags"]
code-aware-javascript = ["code-aware", "dep:tree-sitter", "dep:tree-sitter-javascript", "dep:tree-sitter-tags"]
code-aware-rust       = ["code-aware", "dep:tree-sitter", "dep:tree-sitter-rust",       "dep:tree-sitter-tags"]
```

Then in `[dependencies]`, mark the tree-sitter deps as `optional = true`:

```toml
tree-sitter         = { version = "0.26", optional = true }
tree-sitter-tags    = { version = "0.26", optional = true }
tree-sitter-python  = { version = "0.25", optional = true }
tree-sitter-java    = { version = "0.25", optional = true }
# Should-haves — kept declared so the [features] block compiles; pulled in
# only when their feature flag is enabled. Add in Tasks 11+ if/when needed.
tree-sitter-go         = { version = "0.25", optional = true }
tree-sitter-typescript = { version = "0.25", optional = true }
tree-sitter-javascript = { version = "0.25", optional = true }
tree-sitter-rust       = { version = "0.25", optional = true }
```

(Verify exact versions on crates.io; the brief says 0.26.x for runtime — grammar crates are typically pinned to a matching range.)

- [ ] **Step 2: Verify matrix builds**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
# No features — should NOT pull tree-sitter
cargo build 2>&1 | tail -3

# Python only
cargo build --features code-aware-python 2>&1 | tail -3

# Both must-haves
cargo build --features "code-aware-python code-aware-java" 2>&1 | tail -3

# Umbrella alone is fine (no grammars, but the feature compiles)
cargo build --features code-aware 2>&1 | tail -3
```

Each should print `Finished ...` with no errors. If a feature combination fails because a grammar dep version mismatch (tree-sitter 0.26 ABI vs tree-sitter-python 0.25 ABI, etc.), look at crates.io for the latest aligned set and pin accordingly.

- [ ] **Step 3: Commit**

```bash
git add rust/chunkshop/Cargo.toml
git commit -m "feat(rm-c): add code-aware feature matrix to Cargo.toml (SC-006 scaffold)

Per-grammar flags: code-aware-{python,java} must-have; -{go,typescript,
javascript,rust} should-have. Umbrella code-aware feature exists but is
implied by per-grammar flags. All tree-sitter deps marked optional so
default build is unaffected. Binary-size delta table goes in README in
Task 15."
```

---

## Task 11: Python grammar extractor — `extract_symbols_python` (SC-004 part 1)

**Files:**
- Create: `rust/chunkshop/src/codeparse/langs/mod.rs`
- Create: `rust/chunkshop/src/codeparse/langs/python.rs`
- Modify: `rust/chunkshop/src/codeparse/mod.rs` (verify `pub mod langs;` is already there from Task 3)

- [ ] **Step 1: Create the langs dispatcher module**

`rust/chunkshop/src/codeparse/langs/mod.rs`:

```rust
//! Per-language tree-sitter symbol extractors.
//!
//! Each `langs::<lang>` module is feature-gated. The umbrella `code-aware`
//! feature is implied by any per-grammar feature; turning on
//! `code-aware-python` pulls in `tree-sitter` + `tree-sitter-python` + the
//! Python extractor.

#[cfg(feature = "code-aware-python")]
pub mod python;

#[cfg(feature = "code-aware-java")]
pub mod java;
```

- [ ] **Step 2: Write the Python extractor**

`rust/chunkshop/src/codeparse/langs/python.rs`:

```rust
//! Python source-code symbol extractor via tree-sitter.
//!
//! Mirrors `python/src/chunkshop/codeparse/langs/python.py` (the
//! `extract_symbols` function near line 90 and the tree-sitter Query pattern
//! at line 170). Output `Symbol`s feed `SymbolAwareChunker` and have
//! identical `fqn` / `node_id` to Python's emission for the same input.

use crate::codeparse::{build_fqn, code_symbol_node_id, Symbol};

/// Tree-sitter tags-style query for Python. Captures function defs, class
/// defs, and method defs (functions nested inside classes). Mirror of
/// Python's symbol-extraction query.
const PYTHON_TAGS_QUERY: &str = r#"
(function_definition
  name: (identifier) @function.name) @function.def

(class_definition
  name: (identifier) @class.name) @class.def

(class_definition
  body: (block
    (function_definition
      name: (identifier) @method.name) @method.def))
"#;

/// Extract symbols from Python source. Returns symbols with `fqn` +
/// `parent_name` set; `node_id` derivation is up to the caller (the
/// chunker stamps it onto chunk metadata).
///
/// Note: tree-sitter is error-tolerant — it returns a (partial) tree for
/// malformed Python. The chunker layer is responsible for falling back to
/// `sentence_aware` via `root.has_error()` (see Task 14).
pub fn extract_symbols(file_path: &str, source: &str) -> Vec<Symbol> {
    use tree_sitter::{Parser, Query, QueryCursor};

    let mut parser = Parser::new();
    let language = tree_sitter_python::LANGUAGE.into();
    if parser.set_language(&language).is_err() {
        return Vec::new();
    }
    let Some(tree) = parser.parse(source.as_bytes(), None) else {
        return Vec::new();
    };
    let root = tree.root_node();

    let Ok(query) = Query::new(&language, PYTHON_TAGS_QUERY) else {
        return Vec::new();
    };
    let mut cursor = QueryCursor::new();

    let mut symbols: Vec<Symbol> = Vec::new();
    let source_bytes = source.as_bytes();

    // Walk captures. For each method.name, find its enclosing class to set
    // parent_name. For function.name and class.name, parent_name is None
    // unless the node is nested inside a class (then it's a method, handled
    // by the method.* pattern above).
    let matches = cursor.matches(&query, root, source_bytes);
    for m in matches {
        for capture in m.captures {
            let capture_name = &query.capture_names()[capture.index as usize];
            let node = capture.node;
            let name = node
                .utf8_text(source_bytes)
                .unwrap_or("")
                .to_string();

            let (symbol_type, parent_name) = match *capture_name {
                "function.name" => {
                    // Skip if this is also a method (already captured above).
                    if is_inside_class(node) {
                        continue;
                    }
                    ("function", None)
                }
                "class.name" => ("class", None),
                "method.name" => {
                    let parent = enclosing_class_name(node, source_bytes);
                    ("method", parent)
                }
                _ => continue,
            };

            let fqn = build_fqn(file_path, &name, parent_name.as_deref());
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;

            symbols.push(Symbol {
                name,
                fqn,
                symbol_type: symbol_type.to_string(),
                line_start,
                line_end,
                parent_name,
            });
        }
    }

    symbols
}

fn is_inside_class(node: tree_sitter::Node) -> bool {
    let mut current = node.parent();
    while let Some(p) = current {
        if p.kind() == "class_definition" {
            return true;
        }
        current = p.parent();
    }
    false
}

fn enclosing_class_name(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    let mut current = node.parent();
    while let Some(p) = current {
        if p.kind() == "class_definition" {
            let name_node = p.child_by_field_name("name")?;
            return name_node.utf8_text(source).ok().map(String::from);
        }
        current = p.parent();
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_top_level_function() {
        let src = "def hello():\n    pass\n";
        let syms = extract_symbols("test.py", src);
        assert_eq!(syms.len(), 1);
        assert_eq!(syms[0].name, "hello");
        assert_eq!(syms[0].symbol_type, "function");
        assert_eq!(syms[0].parent_name, None);
        assert_eq!(syms[0].fqn, "test.hello");
    }

    #[test]
    fn extracts_class_and_methods() {
        let src = "class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n";
        let syms = extract_symbols("test.py", src);
        let names: Vec<&str> = syms.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"Foo"));
        assert!(names.contains(&"bar"));
        assert!(names.contains(&"baz"));
        let method_bar = syms.iter().find(|s| s.name == "bar").unwrap();
        assert_eq!(method_bar.symbol_type, "method");
        assert_eq!(method_bar.parent_name.as_deref(), Some("Foo"));
        assert_eq!(method_bar.fqn, "test.Foo.bar");
    }

    #[test]
    fn empty_source_returns_no_symbols() {
        let syms = extract_symbols("empty.py", "");
        assert!(syms.is_empty());
    }
}
```

- [ ] **Step 3: Run Python extractor tests with the feature enabled**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo test --package chunkshop --features code-aware-python --lib codeparse::langs::python 2>&1 | tail -10
```

Expected: 3 tests pass.

If `tree-sitter-python::LANGUAGE` doesn't exist (depends on crate version): older crates use `language()` as a fn (`tree_sitter_python::language()`). Newer use `LANGUAGE` constant. Adjust to whatever the installed version exposes — check `cargo doc --open --features code-aware-python` if unsure.

- [ ] **Step 4: Cross-check against Python's extract output**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
uv run --no-sync python -c "
from chunkshop.codeparse import parse_file
import tempfile, os
src = 'class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n'
with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False) as f:
    f.write(src); p = f.name
r = parse_file(p, language='python', project_id='default')
for s in r.symbols:
    print(f'{s.name:<10} type={s.symbol_type:<10} parent={s.parent_name!r:<10} fqn={s.fqn!r}')
os.unlink(p)
"
```

Compare to your Rust extract output for the same source. The set of `(name, symbol_type, parent_name, fqn)` tuples should match. Order may differ — symbols are a set, not a sequence, in semantic terms.

If symbols diverge: investigate which side has it right. Python is the spec — usually Rust needs adjustment. Common gotchas: tree-sitter capture names slightly differ between crate versions; nested-function detection (Python skips `is_inside_class` for the function-name capture only — your Rust does the same).

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/codeparse/langs/mod.rs rust/chunkshop/src/codeparse/langs/python.rs
git commit -m "feat(rm-c): Python tree-sitter symbol extractor (SC-004 part 1)

extract_symbols(file_path, source) walks a tree-sitter parse of Python
source and emits Symbol{name, fqn, symbol_type, line_start, line_end,
parent_name} for top-level functions, classes, and methods (functions
nested inside classes). Gated by code-aware-python feature.

Mirrors python/src/chunkshop/codeparse/langs/python.py extract_symbols.
Cross-port symbol equivalence is asserted in the E2E test in Task 16."
```

---

## Task 12: Java grammar extractor — `extract_symbols_java` (SC-004 part 2)

**Files:**
- Create: `rust/chunkshop/src/codeparse/langs/java.rs`

- [ ] **Step 1: Read Python's Java extractor to mirror the query**

```bash
cat /home/yonk/yonk-tools/chunkshop-rm-c/python/src/chunkshop/codeparse/langs/java.py | head -60
```

Capture: the tree-sitter query string (similar to `_TAGS_QUERY` in Python), the capture names used (e.g., `@class.name`, `@method.name`), and the parent-class resolution logic.

- [ ] **Step 2: Write the Java extractor**

`rust/chunkshop/src/codeparse/langs/java.rs`:

```rust
//! Java source-code symbol extractor via tree-sitter.
//!
//! Mirrors `python/src/chunkshop/codeparse/langs/java.py`. Captures class
//! defs, interface defs, and method defs (with parent class for the parent_name field).

use crate::codeparse::{build_fqn, Symbol};

const JAVA_TAGS_QUERY: &str = r#"
(class_declaration
  name: (identifier) @class.name) @class.def

(interface_declaration
  name: (identifier) @interface.name) @interface.def

(method_declaration
  name: (identifier) @method.name) @method.def
"#;

pub fn extract_symbols(file_path: &str, source: &str) -> Vec<Symbol> {
    use tree_sitter::{Parser, Query, QueryCursor};

    let mut parser = Parser::new();
    let language = tree_sitter_java::LANGUAGE.into();
    if parser.set_language(&language).is_err() {
        return Vec::new();
    }
    let Some(tree) = parser.parse(source.as_bytes(), None) else {
        return Vec::new();
    };
    let root = tree.root_node();

    let Ok(query) = Query::new(&language, JAVA_TAGS_QUERY) else {
        return Vec::new();
    };
    let mut cursor = QueryCursor::new();
    let mut symbols: Vec<Symbol> = Vec::new();
    let source_bytes = source.as_bytes();

    let matches = cursor.matches(&query, root, source_bytes);
    for m in matches {
        for capture in m.captures {
            let capture_name = &query.capture_names()[capture.index as usize];
            let node = capture.node;
            let name = node
                .utf8_text(source_bytes)
                .unwrap_or("")
                .to_string();

            let (symbol_type, parent_name) = match *capture_name {
                "class.name" => ("class", None),
                "interface.name" => ("interface", None),
                "method.name" => {
                    let parent = enclosing_class_or_interface_name(node, source_bytes);
                    ("method", parent)
                }
                _ => continue,
            };

            let fqn = build_fqn(file_path, &name, parent_name.as_deref());
            let line_start = node.start_position().row as u32 + 1;
            let line_end = node.end_position().row as u32 + 1;

            symbols.push(Symbol {
                name,
                fqn,
                symbol_type: symbol_type.to_string(),
                line_start,
                line_end,
                parent_name,
            });
        }
    }

    symbols
}

fn enclosing_class_or_interface_name(
    node: tree_sitter::Node,
    source: &[u8],
) -> Option<String> {
    let mut current = node.parent();
    while let Some(p) = current {
        let kind = p.kind();
        if kind == "class_declaration" || kind == "interface_declaration" {
            let name_node = p.child_by_field_name("name")?;
            return name_node.utf8_text(source).ok().map(String::from);
        }
        current = p.parent();
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_class_and_method() {
        let src = "public class Foo {\n    public void bar() {}\n}\n";
        let syms = extract_symbols("Foo.java", src);
        let names: Vec<&str> = syms.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"Foo"));
        assert!(names.contains(&"bar"));

        let method_bar = syms.iter().find(|s| s.name == "bar").unwrap();
        assert_eq!(method_bar.symbol_type, "method");
        assert_eq!(method_bar.parent_name.as_deref(), Some("Foo"));
        assert_eq!(method_bar.fqn, "Foo.Foo.bar");
    }

    #[test]
    fn extracts_interface() {
        let src = "public interface Greeter {\n    void greet();\n}\n";
        let syms = extract_symbols("Greeter.java", src);
        let names: Vec<&str> = syms.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"Greeter"));
        // greet() is a method inside interface
        let greet = syms.iter().find(|s| s.name == "greet").unwrap();
        assert_eq!(greet.parent_name.as_deref(), Some("Greeter"));
    }
}
```

- [ ] **Step 3: Run tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo test --package chunkshop --features code-aware-java --lib codeparse::langs::java 2>&1 | tail -10
```

Expected: 2 tests pass.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/src/codeparse/langs/java.rs
git commit -m "feat(rm-c): Java tree-sitter symbol extractor (SC-004 part 2)

Mirrors python/src/chunkshop/codeparse/langs/java.py. Captures classes,
interfaces, and methods (with parent_name = enclosing class/interface).
Gated by code-aware-java feature."
```

---

## Task 13: `SymbolAwareChunker` + `ChunkerConfig` integration

**Files:**
- Create: `rust/chunkshop/src/chunkers/mod.rs` (if not already present — check)
- Create: `rust/chunkshop/src/chunkers/symbol_aware.rs`
- Modify: `rust/chunkshop/src/config.rs` (add `SymbolAware` variant)
- Modify: `rust/chunkshop/src/chunker.rs` (extend `build_chunker` factory)
- Modify: `rust/chunkshop/src/lib.rs` (pub mod chunkers; — check if needed)

This is the structurally largest task. ~150 LOC of new chunker code, plus enum + factory wiring.

- [ ] **Step 1: Check existing chunker module structure**

```bash
ls /home/yonk/yonk-tools/chunkshop-rm-c/rust/chunkshop/src/chunkers/ 2>/dev/null || echo "no chunkers/ dir yet"
grep -nE "mod chunker|mod chunkers" /home/yonk/yonk-tools/chunkshop-rm-c/rust/chunkshop/src/lib.rs
```

If `chunkers/` doesn't exist (it doesn't based on RM-A/RM-B's flat structure — `chunker.rs` is a single file at root), CREATE it. Move the existing chunker implementations to `chunkers/mod.rs` re-exports? **No** — that's a refactor outside this brief's scope. Instead, add `chunkers/` as a sibling module solely for the new code-aware chunker:

```rust
// In rust/chunkshop/src/lib.rs, add:
#[cfg(feature = "code-aware")]
pub mod chunkers {
    pub mod symbol_aware;
}
```

This keeps the existing flat `chunker.rs` untouched and puts new code in a feature-gated module.

- [ ] **Step 2: Write `SymbolAwareChunker`**

`rust/chunkshop/src/chunkers/symbol_aware.rs`:

```rust
//! Symbol-aware chunker — splits source-code documents at symbol boundaries
//! via the per-language extractors in `chunkshop::codeparse::langs`.
//!
//! Mirrors `python/src/chunkshop/chunkers/symbol_aware.py`. Each emitted
//! chunk's `original_content` is the raw source slice for that symbol;
//! `embedded_content` is the same slice (no import-block prefix in v1 —
//! Python's import_block framing is a follow-up).
//!
//! Chunk metadata stamps `fqn`, `node_id`, `language`, `parent_name`,
//! `symbol_name`, `symbol_type`, `line_start`, `line_end`, and
//! `strategy = "symbol_aware"`. On syntax error, falls back to
//! `sentence_aware` and stamps `strategy = "symbol_aware_fallback"`
//! (see Task 14).

use crate::codeparse::{code_symbol_node_id, Symbol};
use crate::config::SymbolAwareChunkerConfig;
use crate::sources::Document;
use serde_json::json;

pub struct SymbolAwareChunker {
    cfg: SymbolAwareChunkerConfig,
}

impl SymbolAwareChunker {
    pub fn new(cfg: SymbolAwareChunkerConfig) -> Self {
        Self { cfg }
    }

    pub fn chunk(&self, doc: &Document) -> Vec<crate::chunker::Chunk> {
        let language = match detect_language(doc) {
            Some(l) => l,
            None => return self.fallback(doc, "language_undetected"),
        };

        let content = doc.content.as_str();
        let symbols = extract_symbols_for_language(&language, &doc.id, content);

        if symbols.is_empty() {
            return self.fallback(doc, "no_symbols");
        }

        let project_id = self
            .cfg
            .project_id
            .clone()
            .unwrap_or_else(|| "default".to_string());

        let mut chunks: Vec<crate::chunker::Chunk> = Vec::with_capacity(symbols.len());
        let lines: Vec<&str> = content.lines().collect();

        for (seq_idx, sym) in symbols.iter().enumerate() {
            let source_slice = slice_lines(&lines, sym.line_start, sym.line_end);
            let node_id = code_symbol_node_id(&project_id, &language, &doc.id, &sym.fqn);

            let metadata = json!({
                "strategy": "symbol_aware",
                "fqn": sym.fqn,
                "node_id": node_id,
                "language": language,
                "parent_name": sym.parent_name,
                "symbol_name": sym.name,
                "symbol_type": sym.symbol_type,
                "line_start": sym.line_start,
                "line_end": sym.line_end,
            });

            chunks.push(crate::chunker::Chunk {
                doc_id: doc.id.clone(),
                seq_num: seq_idx as u32,
                original_content: source_slice.clone(),
                embedded_content: source_slice,
                metadata,
                ..Default::default()  // tags, source_tag etc.
            });
        }

        chunks
    }

    fn fallback(&self, doc: &Document, _reason: &str) -> Vec<crate::chunker::Chunk> {
        // Filled in Task 14 (SC-005 fallback path)
        Vec::new()
    }
}

fn detect_language(doc: &Document) -> Option<String> {
    // Try metadata `path` / `source_path` first, then doc.id. Mirrors Python
    // symbol_aware._detect_language_from_meta at lines 58-78.
    for key in ["path", "source_path"] {
        if let Some(val) = doc.metadata.get(key).and_then(|v| v.as_str()) {
            if let Some(lang) = lang_from_extension(val) {
                return Some(lang);
            }
        }
    }
    lang_from_extension(&doc.id)
}

fn lang_from_extension(path: &str) -> Option<String> {
    let ext = std::path::Path::new(path).extension()?.to_str()?;
    match ext {
        "py" => Some("python".to_string()),
        "java" => Some("java".to_string()),
        _ => None,  // Go/TS/JS/Rust added in follow-up tasks
    }
}

fn extract_symbols_for_language(language: &str, file_path: &str, source: &str) -> Vec<Symbol> {
    match language {
        #[cfg(feature = "code-aware-python")]
        "python" => crate::codeparse::langs::python::extract_symbols(file_path, source),
        #[cfg(feature = "code-aware-java")]
        "java" => crate::codeparse::langs::java::extract_symbols(file_path, source),
        _ => Vec::new(),
    }
}

fn slice_lines(lines: &[&str], start_1based: u32, end_1based: u32) -> String {
    let s = (start_1based.saturating_sub(1)) as usize;
    let e = std::cmp::min(end_1based as usize, lines.len());
    if s >= e {
        return String::new();
    }
    lines[s..e].join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(feature = "code-aware-python")]
    #[test]
    fn chunks_python_at_symbol_boundaries() {
        let doc = Document {
            id: "test.py".to_string(),
            content: "def hello():\n    pass\n\nclass Foo:\n    def bar(self):\n        pass\n".to_string(),
            ..Default::default()
        };
        let cfg = SymbolAwareChunkerConfig::default();
        let chunker = SymbolAwareChunker::new(cfg);
        let chunks = chunker.chunk(&doc);

        // 3 symbols: hello (function), Foo (class), bar (method)
        assert_eq!(chunks.len(), 3);
        let strategies: Vec<&str> = chunks
            .iter()
            .filter_map(|c| c.metadata.get("strategy").and_then(|s| s.as_str()))
            .collect();
        assert!(strategies.iter().all(|&s| s == "symbol_aware"));
    }
}
```

- [ ] **Step 3: Add `SymbolAwareChunkerConfig` to config.rs**

Find the existing chunker config structs in `rust/chunkshop/src/config.rs` (e.g., `SentenceAwareChunkerConfig` around line 680). Add a sibling struct:

```rust
#[cfg(feature = "code-aware")]
#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct SymbolAwareChunkerConfig {
    /// Optional project_id passed to code_symbol_node_id for scoping.
    /// Defaults to "default" to mirror Python.
    #[serde(default)]
    pub project_id: Option<String>,
}
```

Then add the variant to the `ChunkerConfig` enum at `config.rs:604`:

```rust
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChunkerConfig {
    SentenceAware(SentenceAwareChunkerConfig),
    Hierarchy(HierarchyChunkerConfig),
    FixedOverlap(FixedOverlapChunkerConfig),
    NeighborExpand(NeighborExpandChunkerConfig),
    Semantic(SemanticChunkerConfig),
    SummaryEmbed(SummaryEmbedChunkerConfig),
    HierarchicalSummary(HierarchicalSummaryChunkerConfig),
    Consolidation(ConsolidationChunkerConfig),
    #[cfg(feature = "code-aware")]
    SymbolAware(SymbolAwareChunkerConfig),
}
```

Also extend `ChunkerConfig::name()` (and any other match arms over the enum) to handle the new variant. Search for `match self { ChunkerConfig::SentenceAware` to find all match sites:

```bash
grep -n "ChunkerConfig::" rust/chunkshop/src/config.rs | head -10
```

Add `#[cfg(feature = "code-aware")] ChunkerConfig::SymbolAware(_) => "symbol_aware",` to each match.

- [ ] **Step 4: Wire `build_chunker` factory in `chunker.rs:1498`**

Find the `pub fn build_chunker(cfg: ChunkerConfig)` function, add a new branch at the bottom of the match:

```rust
#[cfg(feature = "code-aware")]
ChunkerConfig::SymbolAware(c) => Ok(Box::new(
    crate::chunkers::symbol_aware::SymbolAwareChunker::new(c)
)),
```

`SymbolAwareChunker` must implement `ChunkerImpl + Send + Sync` — find the trait definition (search `pub trait ChunkerImpl`) and add the impl in `symbol_aware.rs`:

```rust
impl crate::chunker::ChunkerImpl for SymbolAwareChunker {
    fn chunk(&self, doc: &Document) -> Vec<crate::chunker::Chunk> {
        self.chunk(doc)
    }
    // Add any other required trait methods — match whatever the existing
    // impl ChunkerImpl for SentenceAwareChunker uses as a template.
}
```

- [ ] **Step 5: Build with feature enabled**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo build --features "code-aware-python code-aware-java" 2>&1 | tail -10
```

Expected: clean build. If there are errors, they'll be missing trait methods or wrong types — read carefully, follow the compiler's hints.

- [ ] **Step 6: Run the chunker test**

```bash
cargo test --package chunkshop --features "code-aware-python code-aware-java" --lib chunkers::symbol_aware 2>&1 | tail -10
```

Expected: 1+ tests pass.

- [ ] **Step 7: Commit**

```bash
git add rust/chunkshop/src/lib.rs rust/chunkshop/src/config.rs rust/chunkshop/src/chunker.rs rust/chunkshop/src/chunkers/
git commit -m "feat(rm-c): SymbolAwareChunker + ChunkerConfig variant + factory wiring (SC-004)

New chunker emits one chunk per extracted Symbol, stamps metadata with
fqn, node_id, language, parent_name, symbol_name, symbol_type, line range.
Gated by code-aware feature. Falls back to empty Vec on no-symbols /
language-undetected (full fallback to sentence_aware lands in Task 14).

Wires through ChunkerConfig::SymbolAware variant + build_chunker factory.
Existing prose/summarization variants untouched."
```

---

## Task 14: Syntax-error fallback (SC-005)

**Files:**
- Modify: `rust/chunkshop/src/chunkers/symbol_aware.rs` (fill in the `fallback` method)

- [ ] **Step 1: Add tree-sitter ERROR-node check to extractors**

In each `langs/<lang>.rs`, after parsing, set a flag if the tree has errors:

`rust/chunkshop/src/codeparse/langs/python.rs` — add this helper:

```rust
/// Returns `true` if tree-sitter's parse tree contains any ERROR or MISSING
/// nodes. Mirror of Python's `ast.parse(content)` SyntaxError check at
/// `python/src/chunkshop/chunkers/symbol_aware.py:120-132`. Used by the
/// chunker to trigger fallback to sentence_aware.
pub fn has_syntax_errors(source: &str) -> bool {
    use tree_sitter::Parser;

    let mut parser = Parser::new();
    let language = tree_sitter_python::LANGUAGE.into();
    if parser.set_language(&language).is_err() {
        return true;  // Can't parse → treat as error
    }
    parser
        .parse(source.as_bytes(), None)
        .map(|tree| tree.root_node().has_error())
        .unwrap_or(true)
}
```

Same pattern in `langs/java.rs` (replacing the LANGUAGE constant).

- [ ] **Step 2: Wire the fallback in `symbol_aware.rs`**

Replace the empty `fallback` method with:

```rust
fn fallback(&self, doc: &Document, reason: &str) -> Vec<crate::chunker::Chunk> {
    use crate::chunker::SentenceAwareChunker;
    use crate::config::SentenceAwareChunkerConfig;

    let inner = SentenceAwareChunker::new(SentenceAwareChunkerConfig::default());
    let mut chunks = inner.chunk(doc);
    for c in &mut chunks {
        // Stamp strategy override + reason for downstream observability.
        // Mirror of Python's `strategy='symbol_aware_fallback'` semantics
        // at python/src/chunkshop/chunkers/symbol_aware.py:120-132.
        if let Some(obj) = c.metadata.as_object_mut() {
            obj.insert("strategy".to_string(), json!("symbol_aware_fallback"));
            obj.insert("fallback_reason".to_string(), json!(reason));
        }
    }
    chunks
}
```

Also extend `chunk()` to detect Python syntax errors specifically (matching Python's `_python_has_syntax_error` check):

```rust
pub fn chunk(&self, doc: &Document) -> Vec<crate::chunker::Chunk> {
    let language = match detect_language(doc) {
        Some(l) => l,
        None => return self.fallback(doc, "language_undetected"),
    };

    let content = doc.content.as_str();

    // Python-specific: tree-sitter is error-tolerant, so check has_error
    // explicitly. Matches python/src/chunkshop/chunkers/symbol_aware.py.
    #[cfg(feature = "code-aware-python")]
    if language == "python"
        && crate::codeparse::langs::python::has_syntax_errors(content)
    {
        return self.fallback(doc, "python_syntax_error");
    }

    let symbols = extract_symbols_for_language(&language, &doc.id, content);

    if symbols.is_empty() {
        return self.fallback(doc, "no_symbols");
    }

    // ... rest of chunk() body unchanged from Task 13
}
```

- [ ] **Step 3: Test fallback behavior**

Add to the test module in `symbol_aware.rs`:

```rust
#[cfg(feature = "code-aware-python")]
#[test]
fn falls_back_on_python_syntax_error() {
    let doc = Document {
        id: "broken.py".to_string(),
        content: "def hello(\n    # missing close paren, no body\n".to_string(),
        ..Default::default()
    };
    let chunker = SymbolAwareChunker::new(SymbolAwareChunkerConfig::default());
    let chunks = chunker.chunk(&doc);

    assert!(!chunks.is_empty(), "fallback should produce sentence_aware chunks, not empty");
    let strategy = chunks[0]
        .metadata
        .get("strategy")
        .and_then(|s| s.as_str())
        .unwrap_or("");
    assert_eq!(strategy, "symbol_aware_fallback");
}

#[test]
fn falls_back_on_unknown_language() {
    let doc = Document {
        id: "mystery.xyz".to_string(),
        content: "some random content here\n".to_string(),
        ..Default::default()
    };
    let chunker = SymbolAwareChunker::new(SymbolAwareChunkerConfig::default());
    let chunks = chunker.chunk(&doc);

    assert!(!chunks.is_empty(), "unknown language should still produce chunks (via sentence_aware)");
    let strategy = chunks[0]
        .metadata
        .get("strategy")
        .and_then(|s| s.as_str())
        .unwrap_or("");
    assert_eq!(strategy, "symbol_aware_fallback");
}
```

- [ ] **Step 4: Run tests**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo test --package chunkshop --features "code-aware-python code-aware-java" --lib chunkers::symbol_aware 2>&1 | tail -10
```

Expected: all chunker tests pass including the 2 new fallback ones.

- [ ] **Step 5: Commit**

```bash
git add rust/chunkshop/src/chunkers/symbol_aware.rs rust/chunkshop/src/codeparse/langs/
git commit -m "feat(rm-c): syntax-error fallback to sentence_aware (SC-005)

When tree-sitter returns an error-containing tree for Python source, or
when the language can't be detected from the doc, SymbolAwareChunker
falls back to SentenceAwareChunker and stamps strategy=symbol_aware_fallback
on each emitted chunk. Mirrors Python's behavior at
python/src/chunkshop/chunkers/symbol_aware.py:120-132.

Also stamps fallback_reason for observability (language_undetected,
python_syntax_error, no_symbols)."
```

---

## Task 15: ⛔ DC-003 grammar coord + feature matrix builds + README size table (SC-006)

**Files:**
- Modify: `rust/chunkshop/README.md`

**Purpose:** Drift Checkpoint DC-003 — before considering should-have grammars, check Python chunkshop#40 status (tree-sitter migration for Go/TS/JS). The brief says: if #40 lands first, Rust should match the NEW Python tree-sitter shape, not the legacy regex fallback.

- [ ] **Step 1: DC-003 — check chunkshop#40 status**

```bash
gh issue view 40 --repo yonk-labs/chunkshop --json state,title -q '{state: .state, title: .title}'
```

If `state: CLOSED` → Python has tree-sitter for Go/TS/JS now. Rust's should-have feature flags should mirror the new Python shape. This means re-running the same audit pattern (Task 2 DC-001) against the new `python/src/chunkshop/codeparse/langs/{go,typescript,javascript}.py` files before writing the Rust extractors.

If `state: OPEN` → defer Go/TS/JS extractor implementation. The feature flags are already declared in Cargo.toml (Task 10) as placeholders; ship without their extractors implemented. They'll fail to compile if a user tries to enable them — that's fine for v1; document in README.

For this plan, default assumption: chunkshop#40 is OPEN → DEFER Go/TS/JS extractors. Note for future implementer to revisit.

- [ ] **Step 2: Run the full feature matrix to confirm SC-006 compliance**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust

echo "=== default (no features) ==="
cargo build --release 2>&1 | tail -3
ls -la target/release/chunkshop | awk '{print $5, $9}'

echo "=== code-aware-python only ==="
cargo build --release --features code-aware-python 2>&1 | tail -3
ls -la target/release/chunkshop | awk '{print $5, $9}'

echo "=== code-aware-python + code-aware-java ==="
cargo build --release --features "code-aware-python code-aware-java" 2>&1 | tail -3
ls -la target/release/chunkshop | awk '{print $5, $9}'

echo "=== umbrella code-aware ==="
cargo build --release --features code-aware 2>&1 | tail -3
ls -la target/release/chunkshop | awk '{print $5, $9}'
```

Capture the binary sizes from each build. The default-features build should NOT include tree-sitter (its dep is `optional = true`); a `cargo tree -p chunkshop` confirms.

- [ ] **Step 3: Write the README section**

Open `rust/chunkshop/README.md` (or create if missing — minimal stub OK). Add a new section:

```markdown
## Code-aware chunking

The `code-aware` feature enables symbol-aware chunking for source code via
[tree-sitter](https://crates.io/crates/tree-sitter). Each grammar is
opt-in to control binary size.

### Feature matrix

| Feature flag | Languages | Binary size delta vs default |
|---|---|---|
| `default` (none) | — | (baseline) |
| `code-aware-python` | Python | +X MB |
| `code-aware-java` | Java | +X MB |
| `code-aware-python,code-aware-java` | Python + Java | +X MB |
| `code-aware` (umbrella) | All must-have | +X MB |
| `code-aware-go` | Go (should-have, pending [chunkshop#40](https://github.com/yonk-labs/chunkshop/issues/40)) | +X MB |
| `code-aware-typescript` | TypeScript (should-have) | +X MB |
| `code-aware-javascript` | JavaScript (should-have) | +X MB |
| `code-aware-rust` | Rust (should-have) | +X MB |

(Sizes captured on `<your platform>` with `cargo build --release`. Will
drift over time — run `cargo build --release --features <flags>` to
re-measure.)

### Cross-port byte-equivalence

When the same source file is ingested via chunkshop-py and chunkshop-rs,
the resulting chunks share identical `fqn` and `node_id` metadata. This
is enforced by:

- **Rust proptest** at `rust/chunkshop/tests/cross_port_proptest.rs`
  (~1500 random cases per `cargo test`)
- **Python pytest** at `python/tests/chunkshop/test_rust_cross_port_parity.py`
  (50+ curated vectors, invokes the `fqn-cli` Rust binary as subprocess)

Both gates run in CI on every PR.

### Syntax-error fallback

When tree-sitter returns an error-containing parse tree (mirroring Python's
`ast.parse → SyntaxError` check), the chunker falls back to
`sentence_aware` and stamps `metadata.strategy = "symbol_aware_fallback"`.
```

Fill in the actual `+X MB` numbers from Step 2.

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/README.md
git commit -m "docs(rm-c): document code-aware feature matrix + binary-size table (SC-006)

New README section explains the per-grammar feature flags, includes a
binary-size delta table captured against this PR's build, links to the
cross-port test harness, and notes the syntax-error fallback semantics.

Should-have grammars (Go/TS/JS/Rust) are declared in Cargo.toml but their
extractors aren't implemented — gated on chunkshop#40 (Python's tree-sitter
migration for Go/TS/JS). README marks them accordingly."
```

---

## Task 16: E2E parity test fixtures + configs (SC-004 E2E)

**Files:**
- Create: `python/tests/fixtures/rm-c-parity/sample.py`
- Create: `python/tests/fixtures/rm-c-parity/Foo.java`
- Create: `python/tests/fixtures/rm-c-parity/UserService.py`
- Create: `python/tests/fixtures/rm-c-parity/Greeter.java`
- Create: `docs/samples/rm-c-parity/rm-c-parity-py.yaml`
- Create: `docs/samples/rm-c-parity/rm-c-parity-rs.yaml`

- [ ] **Step 1: Create fixture corpus**

Create 5-8 small source files (~10-30 lines each) at `python/tests/fixtures/rm-c-parity/`. Mix:
- 3-4 Python files with various symbol shapes (top-level functions, classes, methods, nested functions)
- 2-3 Java files (classes, interfaces, methods)
- Optional: 1 malformed Python file to test fallback parity

Examples:

`python/tests/fixtures/rm-c-parity/sample.py`:
```python
"""Sample Python file for RM-C cross-port parity test."""

def hello():
    return "hello"


class Greeter:
    def greet(self, name):
        return f"hello, {name}"

    def shout(self, name):
        return self.greet(name).upper()
```

`python/tests/fixtures/rm-c-parity/UserService.py`:
```python
class UserService:
    def __init__(self, db):
        self.db = db

    def get_user(self, user_id):
        return self.db.find(user_id)
```

`python/tests/fixtures/rm-c-parity/Foo.java`:
```java
public class Foo {
    public void bar() {
        System.out.println("bar");
    }
    public int baz(int x) {
        return x * 2;
    }
}
```

`python/tests/fixtures/rm-c-parity/Greeter.java`:
```java
public interface Greeter {
    String greet(String name);
}
```

- [ ] **Step 2: Create the Python ingest YAML**

`docs/samples/rm-c-parity/rm-c-parity-py.yaml`:

```yaml
# Python-side ingest for RM-C cross-port E2E parity test.
# Consumed by python/tests/chunkshop/test_rm_c_e2e_parity.py.

cell_name: rm_c_parity_py

source:
  type: files
  glob: python/tests/fixtures/rm-c-parity/*
  id_from: stem

chunker:
  type: symbol_aware

embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
  threads: 4

extractor:
  type: none

target:
  type: postgres
  dsn_env: CHUNKSHOP_TEST_DSN
  database: chunkshop_test
  table: rm_c_py
  mode: overwrite
  force_overwrite: true
  hnsw: false

runtime:
  omp_num_threads: 2
  log_path: /tmp/chunkshop-rm-c-py.log
```

- [ ] **Step 3: Create the Rust ingest YAML (identical except target table)**

`docs/samples/rm-c-parity/rm-c-parity-rs.yaml`:

Same as the Python YAML but with `cell_name: rm_c_parity_rs` and `table: rm_c_rs`. Everything else identical so the only delta in the resulting tables is what each port emits for the same input.

- [ ] **Step 4: Validate both configs load**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
uv run --no-sync python -c "
from chunkshop.config import load_config
for p in ['../docs/samples/rm-c-parity/rm-c-parity-py.yaml',
          '../docs/samples/rm-c-parity/rm-c-parity-rs.yaml']:
    cfg = load_config(p)
    print(f'OK: {cfg.cell_name} -> {type(cfg.chunker).__name__}')
"
```

Expected: both load OK with `SymbolAwareChunker` (Python side). The Rust YAML loads via Python's config too — that's fine; Rust ignores the cell_name and reads the same shape.

- [ ] **Step 5: Commit**

```bash
git add python/tests/fixtures/rm-c-parity/ docs/samples/rm-c-parity/
git commit -m "fixture(rm-c): cross-port E2E parity corpus + ingest YAMLs (SC-004 setup)

5-8 small Python + Java source files exercising symbol-aware extraction
across functions, classes, methods, and interfaces. Two ingest YAMLs
(rm-c-parity-py.yaml, rm-c-parity-rs.yaml) configured identically except
for cell_name + target table. Consumed by the E2E test in Task 17."
```

---

## Task 17: E2E parity pytest — Python vs Rust ingest joined on node_id (SC-004)

**Files:**
- Create: `python/tests/chunkshop/test_rm_c_e2e_parity.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end cross-port parity test (SC-004 of the RM-C mission brief).

Ingests the same fixture corpus via chunkshop-py and chunkshop-rs into
separate pgvector tables in `chunkshop_test`. Joins on `node_id`; asserts
row count + per-row metadata tuple equality (excluding the embedding
vector, which differs across embedder calls).

Skips cleanly when:
  - CHUNKSHOP_TEST_DSN is unset
  - Rust chunkshop binary isn't built with code-aware features
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

CHUNKSHOP_TEST_DSN = os.environ.get("CHUNKSHOP_TEST_DSN")
REPO_ROOT = Path(__file__).resolve().parents[3]
RUST_BINARY = REPO_ROOT / "rust" / "target" / "release" / "chunkshop"
PY_CONFIG = REPO_ROOT / "docs" / "samples" / "rm-c-parity" / "rm-c-parity-py.yaml"
RS_CONFIG = REPO_ROOT / "docs" / "samples" / "rm-c-parity" / "rm-c-parity-rs.yaml"

pytestmark = [
    pytest.mark.skipif(
        not CHUNKSHOP_TEST_DSN,
        reason="CHUNKSHOP_TEST_DSN not set",
    ),
    pytest.mark.skipif(
        not RUST_BINARY.exists(),
        reason=f"Rust chunkshop binary not built with code-aware features; "
               f"run `cd rust && cargo build --release --features code-aware`",
    ),
]


def _connect():
    import psycopg
    return psycopg.connect(CHUNKSHOP_TEST_DSN)


def _drop_tables():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS rm_c_py CASCADE;")
            cur.execute("DROP TABLE IF EXISTS rm_c_rs CASCADE;")
        conn.commit()


@pytest.fixture(scope="module")
def ingested_tables():
    """Run Python + Rust ingests once; drop tables on teardown."""
    _drop_tables()

    # Python ingest
    py_result = subprocess.run(
        ["uv", "run", "--no-sync", "chunkshop", "ingest", "--config", str(PY_CONFIG)],
        cwd=REPO_ROOT / "python",
        env={**os.environ, "CHUNKSHOP_TEST_DSN": CHUNKSHOP_TEST_DSN},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if py_result.returncode != 0:
        pytest.fail(f"Python ingest failed: {py_result.stderr[-1500:]}")

    # Rust ingest
    rs_result = subprocess.run(
        [str(RUST_BINARY), "ingest", "--config", str(RS_CONFIG)],
        cwd=REPO_ROOT,
        env={**os.environ, "CHUNKSHOP_TEST_DSN": CHUNKSHOP_TEST_DSN},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if rs_result.returncode != 0:
        pytest.fail(f"Rust ingest failed: {rs_result.stderr[-1500:]}")

    yield ("rm_c_py", "rm_c_rs")

    _drop_tables()


def test_row_count_parity(ingested_tables):
    """Same fixture corpus should produce the same chunk count in both ports."""
    py_table, rs_table = ingested_tables
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {py_table};")
            py_count = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM {rs_table};")
            rs_count = cur.fetchone()[0]
    assert py_count == rs_count, (
        f"row count diverges: python={py_count} rust={rs_count}"
    )
    assert py_count > 0


def test_node_id_set_parity(ingested_tables):
    """The set of node_id values should be identical between the two tables."""
    py_table, rs_table = ingested_tables
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata->>'node_id' FROM {py_table} "
                f"WHERE metadata ? 'node_id';"
            )
            py_ids = {row[0] for row in cur.fetchall()}
            cur.execute(
                f"SELECT metadata->>'node_id' FROM {rs_table} "
                f"WHERE metadata ? 'node_id';"
            )
            rs_ids = {row[0] for row in cur.fetchall()}

    only_py = py_ids - rs_ids
    only_rs = rs_ids - py_ids
    assert not only_py, f"node_ids only in Python: {only_py}"
    assert not only_rs, f"node_ids only in Rust: {only_rs}"


def test_per_node_metadata_parity(ingested_tables):
    """Per node_id, the (fqn, language, symbol_name, parent_name, symbol_type)
    tuple must be identical."""
    py_table, rs_table = ingested_tables
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT metadata->>'node_id', "
                f"       metadata->>'fqn', metadata->>'language', "
                f"       metadata->>'symbol_name', metadata->>'parent_name', "
                f"       metadata->>'symbol_type' "
                f"FROM {py_table} WHERE metadata ? 'node_id' "
                f"ORDER BY metadata->>'node_id';"
            )
            py_rows = {r[0]: r[1:] for r in cur.fetchall()}
            cur.execute(
                f"SELECT metadata->>'node_id', "
                f"       metadata->>'fqn', metadata->>'language', "
                f"       metadata->>'symbol_name', metadata->>'parent_name', "
                f"       metadata->>'symbol_type' "
                f"FROM {rs_table} WHERE metadata ? 'node_id' "
                f"ORDER BY metadata->>'node_id';"
            )
            rs_rows = {r[0]: r[1:] for r in cur.fetchall()}

    divergent = []
    for node_id, py_tuple in py_rows.items():
        rs_tuple = rs_rows.get(node_id)
        if rs_tuple != py_tuple:
            divergent.append((node_id, py_tuple, rs_tuple))

    assert not divergent, (
        f"{len(divergent)} node(s) with divergent metadata:\n" +
        "\n".join(f"  {nid}: py={py!r} rs={rs!r}" for nid, py, rs in divergent[:5])
    )
```

- [ ] **Step 2: Build the Rust binary with code-aware features**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/rust
cargo build --release --features "code-aware-python code-aware-java" 2>&1 | tail -3
```

- [ ] **Step 3: Run the test in skip mode**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
mv ../rust/target/release/chunkshop ../rust/target/release/chunkshop.bak
uv run --no-sync pytest tests/chunkshop/test_rm_c_e2e_parity.py -v 2>&1 | tail -10
mv ../rust/target/release/chunkshop.bak ../rust/target/release/chunkshop
```

Expected: 3 tests SKIPPED with the Rust-binary-not-built reason.

- [ ] **Step 4: Run for real**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c/python
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test
uv run --no-sync pytest tests/chunkshop/test_rm_c_e2e_parity.py -v 2>&1 | tail -30
```

Expected: 3 tests PASS.

If `test_node_id_set_parity` or `test_per_node_metadata_parity` fails: the divergent node_ids / tuples in the assertion message localize the bug. Most likely culprits:
- Different symbol-extraction (e.g., Rust missing a nested function pattern Python catches)
- `parent_name` divergence (e.g., Python returns `None` where Rust returns `""`)
- Different language detection (extension vs metadata fallback ordering)

Fix the Rust side (Python is the spec).

- [ ] **Step 5: Commit**

```bash
git add python/tests/chunkshop/test_rm_c_e2e_parity.py
git commit -m "test(rm-c): E2E cross-port parity asserting node_id-keyed metadata equality (SC-004)

Ingests the rm-c-parity fixture corpus via Python + Rust into separate
pgvector tables (chunkshop_test.{rm_c_py, rm_c_rs}). Asserts:
1. Row count equality between the two tables
2. Identical set of node_id values
3. Per node_id, identical (fqn, language, symbol_name, parent_name,
   symbol_type) tuple

Skipped cleanly when CHUNKSHOP_TEST_DSN unset or Rust binary not built
with code-aware features. Drops tables on teardown."
```

---

## Task 18: ⛔ DC-FINAL coverage audit + PR

**Files:** none modified — pure audit + PR creation.

- [ ] **Step 1: Re-read the mission brief one final time**

```bash
cat skill-output/mission-brief/Mission-Brief-rm-c-rust-code-aware-chunkers.md
```

- [ ] **Step 2: Build the SC evidence table**

| SC | Evidence file/test | Pass? |
|---|---|---|
| SC-001 | `rust/chunkshop/src/codeparse/fqn.rs` + 14 tests via `cargo test --lib codeparse::fqn` | yes / no |
| SC-002 | `rust/chunkshop/src/codeparse/id.rs` + 6 tests via `cargo test --lib codeparse::id` | yes / no |
| SC-003 | `rust/chunkshop/tests/cross_port_proptest.rs` (proptest) + `python/tests/chunkshop/test_rust_cross_port_parity.py` (curated) — both green | yes / no |
| SC-004 | `python/tests/chunkshop/test_rm_c_e2e_parity.py` — 3 tests pass | yes / no |
| SC-005 | `rust/chunkshop/src/chunkers/symbol_aware.rs` `falls_back_on_python_syntax_error` + `falls_back_on_unknown_language` tests | yes / no |
| SC-006 | Cargo `[features]` block + matrix builds in Task 15 + README binary-size table | yes / no |

For each row, run the corresponding test/build and confirm. If any is "no" — STOP, fix the gap before the PR.

- [ ] **Step 3: Out-of-Scope drift audit**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c
git diff main...HEAD --name-only
```

Expected file list (anything outside this set is a drift):
- `rust/chunkshop/Cargo.toml`
- `rust/chunkshop/src/codeparse/{mod,fqn,id,symbol}.rs`
- `rust/chunkshop/src/codeparse/langs/{mod,python,java}.rs`
- `rust/chunkshop/src/bin/fqn-cli.rs`
- `rust/chunkshop/src/chunkers/symbol_aware.rs`
- `rust/chunkshop/src/lib.rs` (mod + cfg-gated re-exports)
- `rust/chunkshop/src/config.rs` (enum variant + struct only)
- `rust/chunkshop/src/chunker.rs` (factory branch only)
- `rust/chunkshop/tests/cross_port_proptest.rs`
- `rust/chunkshop/README.md`
- `python/tests/chunkshop/test_rust_cross_port_parity.py`
- `python/tests/chunkshop/test_rm_c_e2e_parity.py`
- `python/tests/fixtures/rm-c-parity/*`
- `docs/samples/rm-c-parity/*.yaml`

**Anything else is drift.** Specifically watch for:
- Modifications under `python/src/chunkshop/` (out of scope — those are tracked in chunkshop#40, #41)
- Modifications to existing Rust chunker variants (must be additive)
- New deps not in the approved list (`tree-sitter`, `tree-sitter-tags`, per-grammar crates, `sha1`, `proptest`)
- A pyo3 / maturin bridge (explicitly rejected in the brief)

If any drift → STOP, revert, re-commit cleanly.

- [ ] **Step 4: Run the full test matrix one final time**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c

# Rust: all tests in all relevant feature combinations
cd rust
cargo test --workspace 2>&1 | tail -10  # default features
cargo test --workspace --features "code-aware-python code-aware-java" 2>&1 | tail -10
cd ..

# Python: all tests with test DSN
cd python
export CHUNKSHOP_TEST_DSN=postgresql://postgres:postgres@localhost:5434/chunkshop_test
uv run --no-sync pytest -q 2>&1 | tail -6
cd ..
```

Expected: all green. Document any pre-existing failures (not from this branch) in the PR description so they're not surprising.

- [ ] **Step 5: Push branch**

```bash
cd /home/yonk/yonk-tools/chunkshop-rm-c
git push -u origin feat/rm-c
```

- [ ] **Step 6: Open PR**

```bash
gh pr create --title "feat(rm-c): Rust port of code-aware + symbol-aware chunkers" --body "$(cat <<'EOF'
## Summary

Rust port of chunkshop's `code_aware` + `symbol_aware` chunkers, achieving byte-equivalent `fqn` + `node_id` emission so chunkshop-rs and chunkshop-py write interchangeable rows into shared pgvector tables.

- **Foundations:** `build_fqn` + `code_symbol_node_id` ported with TDD (14 + 6 mirror tests against `python/tests/chunkshop/codeparse/`)
- **Cross-port harness:** Rust `proptest` (~1500 random cases) + Python pytest invoking a new `fqn-cli` Rust binary (48+ curated vectors)
- **Symbol extraction:** `tree-sitter` + `tree-sitter-{python,java,tags}` mirroring Python's `Query` / `QueryCursor` pattern at `langs/python.py:170` and `langs/java.py:145`
- **Fallback:** tree-sitter `root.has_error()` triggers fallback to `sentence_aware` with `strategy='symbol_aware_fallback'`, mirroring Python's `ast.parse → SyntaxError` check
- **E2E:** new fixture corpus + Python/Rust ingest YAMLs + pytest joining on `node_id` between Python + Rust pgvector tables

Mission brief: `skill-output/mission-brief/Mission-Brief-rm-c-rust-code-aware-chunkers.md`
Plan: `docs/superpowers/plans/2026-05-28-rm-c-rust-code-aware-chunkers.md`
Research: `skill-output/research-and-design/Research-Report-rust-code-aware-chunking.md`

## SC coverage

- **SC-001** ✅ `rust/chunkshop/src/codeparse/fqn.rs` — 14 mirror tests of `test_fqn.py`
- **SC-002** ✅ `rust/chunkshop/src/codeparse/id.rs` — 6 mirror tests of `test_id.py`
- **SC-003** ✅ `rust/chunkshop/tests/cross_port_proptest.rs` (6 proptest invariants) + `python/tests/chunkshop/test_rust_cross_port_parity.py` (48+ curated vectors)
- **SC-004** ✅ `python/tests/chunkshop/test_rm_c_e2e_parity.py` — 3 tests pass (row count, node_id set, per-row metadata tuple)
- **SC-005** ✅ Fallback path in `symbol_aware.rs` + tests `falls_back_on_python_syntax_error` + `falls_back_on_unknown_language`
- **SC-006** ✅ Cargo `[features]` matrix + README "Code-aware chunking" section with binary-size delta table

## Drift checkpoints

- **DC-001** ✅ ground-truth audit of Python `fqn.py` / `id.py` + tests (Task 2)
- **DC-002** ✅ foundation parity gate before chunker work (Task 9)
- **DC-003** ✅ chunkshop#40 status checked before adding should-have grammars (Task 15)
- **DC-FINAL** ✅ this PR's coverage audit above

## Out of scope (per brief)

- No changes to `python/src/chunkshop/` (deferred items tracked in chunkshop#40, #41, #42, #43)
- No `ruff_python_parser` / `ra_ap_syntax`
- No pyo3/maturin bridge
- No perf benchmarks Rust vs Python
- No A/B gate consumer code (pg-raggraph#47–50)
- No Go/TS/JS extractors implemented (feature flags declared as placeholders; pending chunkshop#40)

## Pre-requisite

- [x] PR #39 (`build_fqn` OS-dep fix) merged — provides the OS-independent Python spec RM-C mirrors

## Test plan

- [x] `cargo test --workspace` (no features) → green
- [x] `cargo test --workspace --features "code-aware-python code-aware-java"` → green
- [x] `PROPTEST_CASES=2000 cargo test --package chunkshop --test cross_port_proptest` → green
- [x] `pytest python/tests/chunkshop/test_rust_cross_port_parity.py` (with `fqn-cli` built) → green
- [x] `pytest python/tests/chunkshop/test_rm_c_e2e_parity.py` (with test PG + Rust binary built) → green
- [x] Full Python test suite still green (or pre-existing failures documented)
EOF
)"
```

Capture the PR URL.

- [ ] **Step 7: Archive plan (post-merge, not part of this PR)**

After this PR merges, in a follow-up commit on `main`:

```bash
git mv docs/superpowers/plans/2026-05-28-rm-c-rust-code-aware-chunkers.md \
       archive/docs/superpowers/plans/2026-05-28-rm-c-rust-code-aware-chunkers.md
git commit -m "chore: archive shipped RM-C plan"
```

Per `CLAUDE.md` plans convention.
