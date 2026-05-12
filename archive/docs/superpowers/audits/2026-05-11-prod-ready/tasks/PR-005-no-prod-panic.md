# PR-005 — Eliminate production-code `panic!` on `max_chars: 0`

**Priority:** P2
**Effort:** S (~1 hour)
**Dependencies:** none
**GAP-IDs:** GAP-003

## Problem

`rust/chunkshop/src/chunker.rs:369` panics if called with `max_chars: 0`:

```rust
pub fn split_to_max_chars(text: &str, max_chars: usize) -> Vec<String> {
    if max_chars == 0 {
        panic!("max_chars must be positive");
    }
    ...
}
```

YAML-loaded configs are validated at parse time, so this `panic!` is unreachable via the documented CLI path. But it's reachable via programmatic API misuse — a host app constructing a chunker with `max_chars: 0` will crash the process instead of getting a recoverable error.

## Solution

Three options, in increasing effort + correctness:

### Option A — `unreachable!` macro (XS)

If you're confident YAML / pydantic / serde validation guarantees `max_chars > 0` before reaching this function:

```rust
if max_chars == 0 {
    unreachable!("max_chars validated > 0 at config load");
}
```

Same runtime behavior, but tells the reader (and the compiler's exhaustiveness checker) that this branch is impossible.

### Option B — `assert!` with `# Panics` rustdoc block (XS, slightly better DX)

```rust
/// Split text into chunks of at most `max_chars` characters.
///
/// # Panics
///
/// Panics if `max_chars == 0`. Validation is expected at the caller.
pub fn split_to_max_chars(text: &str, max_chars: usize) -> Vec<String> {
    assert!(max_chars > 0, "max_chars must be positive");
    ...
}
```

Same behavior; documented panic contract for downstream API consumers.

### Option C — Return `Result` (M, fully correct)

Change the signature to `Result<Vec<String>, anyhow::Error>` and propagate through all callers:

```rust
pub fn split_to_max_chars(text: &str, max_chars: usize) -> Result<Vec<String>> {
    if max_chars == 0 {
        return Err(anyhow!("max_chars must be positive (got 0)"));
    }
    ...
}
```

Most correct, but ripples through every chunker that calls this helper.

## Recommendation

**Option B** for v0.4.x — documents the contract without API churn. **Option C** as a v0.5 refactor if the chunker stage moves to fallible by default.

## Acceptance Criteria

- [ ] `grep -rn 'panic!' rust/chunkshop/src/` returns no hits outside `#[cfg(test)]` modules and `unreachable!` / `assert!` patterns.
- [ ] If Option B chosen: rustdoc `# Panics` block present.
- [ ] If Option C chosen: all callers of `split_to_max_chars` use `?` or explicit error handling.
- [ ] Existing tests pass.

## Risk if Skipped

Programmatic API misuse crashes the process instead of returning a recoverable error. Library users embedding chunkshop into a long-running host process care; CLI-only users don't notice.
