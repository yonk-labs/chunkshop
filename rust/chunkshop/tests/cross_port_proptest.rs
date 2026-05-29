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
