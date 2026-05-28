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
