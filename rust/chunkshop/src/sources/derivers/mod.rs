//! Media derivers: turn non-text bytes into text plus metadata. Mirrors
//! `python/src/chunkshop/sources/derivers/`. Every deriver's output is
//! stamped with provenance and memoised on (content_hash, derivation_hash)
//! so nothing is derived twice and nothing is written without attribution.
//!
//! Not `media`-gated — `ParseResult`/`Deriver` and provenance are always
//! available (see `sources::mod`'s doc comment); only the format-specific
//! parser submodules under `parsers/` are gated. Following `parsers/mod.rs`'s
//! established convention, a submodule here is declared only by the task
//! that creates its file — `image_meta` (Task 9) and `clip_tags` (a later
//! task) are not declared yet.

pub mod cache;
pub mod provenance;
