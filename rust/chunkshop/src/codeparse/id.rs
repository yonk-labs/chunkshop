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
