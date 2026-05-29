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
    // ("a//b/c.py"); mirrors Python's post-PR #39 behaviour.
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
