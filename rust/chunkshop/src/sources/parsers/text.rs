//! Plain-text passthrough. Always available (no `media` feature needed) —
//! this is the format Rust already reads correctly today, and every
//! existing text-only YAML must keep working unchanged.
//!
//! Deliberate divergence from Python (not fixed here): Python's
//! `TextParser.parse` decodes with `errors="replace"` (lossy UTF-8 decode);
//! `std::fs::read_to_string` below is strict and errors on invalid UTF-8.
//! This asymmetry pre-dates this file — today's Rust `FilesSource` already
//! calls `read_to_string` with the same strictness. Changing it would alter
//! behavior for every existing text YAML containing a malformed-UTF-8 file,
//! which is out of scope for this parser-layer wiring task.

use std::path::Path;

use super::{Deriver, ParseResult};

pub struct TextDeriver;

impl Deriver for TextDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("reading {}: {e}", path.display()))?;
        Ok(ParseResult::new(text))
    }
}
