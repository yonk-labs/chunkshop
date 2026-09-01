//! DOCX -> text via `docx-rust` 0.1.11 (crates.io name for PoiScript's
//! reader — NOT bokuweb's writer-only `docx-rs`, a common crate-name trap
//! per the scoping). Mirrors `python/src/chunkshop/sources/parsers/docx.py`:
//! every paragraph's text, newline-joined.
//!
//! Traversal shape confirmed by reading the vendored source
//! (`~/.cargo/registry/src/*/docx-rust-0.1.11/`), not just docs.rs:
//! `DocxFile::parse()` returns `Docx<'_>` with a `document: Document<'a>`
//! field (`src/docx.rs`); `Document::body: Body<'a>` (`src/document/document.rs`);
//! `Body::content: Vec<BodyContent<'a>>` (`src/document/body.rs`);
//! `BodyContent::Paragraph(Paragraph<'a>)`; `Paragraph::content:
//! Vec<ParagraphContent<'a>>` (`src/document/paragraph.rs`);
//! `ParagraphContent::Run(Run<'a>)`; `Run::content: Vec<RunContent<'a>>`
//! (`src/document/run.rs`); `RunContent::Text(Text<'a>)`; and `Text`'s
//! inner string field IS named `text` (`pub text: Cow<'a, str>` in
//! `src/document/text.rs`) — the plan's `.text` guess was correct.

use std::path::Path;

use docx_rust::document::{BodyContent, ParagraphContent, RunContent};
use docx_rust::DocxFile;

use super::{Deriver, ParseResult};

pub struct DocxDeriver;

impl Deriver for DocxDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let docx_file = DocxFile::from_file(path)
            .map_err(|e| anyhow::anyhow!("failed to open DOCX {}: {e}", path.display()))?;
        let docx = docx_file
            .parse()
            .map_err(|e| anyhow::anyhow!("failed to parse DOCX {}: {e}", path.display()))?;

        let mut paragraphs = Vec::new();
        for content in &docx.document.body.content {
            if let BodyContent::Paragraph(p) = content {
                let mut text = String::new();
                for pc in &p.content {
                    if let ParagraphContent::Run(run) = pc {
                        for rc in &run.content {
                            if let RunContent::Text(t) = rc {
                                text.push_str(&t.text);
                            }
                        }
                    }
                }
                paragraphs.push(text);
            }
        }
        Ok(ParseResult::new(paragraphs.join("\n")))
    }
}
