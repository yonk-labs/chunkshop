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
    /// Python's `TextParser` (`parsers/__init__.py`'s `_TEXT`, the table's
    /// default and its fallback for any unknown extension).
    fn parser_name(&self) -> &'static str {
        "TextParser"
    }

    /// Never makes a `had_content` claim, and must never learn how to.
    /// Bytes in, text out: a zero-byte or whitespace-only file is
    /// LEGITIMATELY empty, and nothing was lost extracting it. That is what
    /// keeps `on_empty` off the plain-text path no matter what it is set to
    /// — see `ParseResult::had_content` and `FilesSourceConfig::on_empty`.
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| anyhow::anyhow!("reading {}: {e}", path.display()))?;
        Ok(ParseResult::new(text))
    }
}
