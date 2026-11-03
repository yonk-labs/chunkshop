//! Optional add-on for `chunkshop-rs`: turns files (text today; PDF/DOCX/PPTX/XLSX
//! as slices land) into core `Document`s. Core is untouched; feed the result to
//! `Pipeline::ingest_document`. Mirrors Python `chunkshop.sources.files` + parsers
//! (`metadata.parser` uses the Python class names).
//!
//! Local-only: no network, no external services, no subprocesses.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use chunkshop::config::FilesSourceConfig;
use chunkshop::sources::Document;
use serde_json::json;
use sha1::{Digest, Sha1};

#[cfg(any(feature = "docx", feature = "pptx", feature = "odf"))]
mod ooxml;
#[cfg(feature = "html")]
mod html;
#[cfg(feature = "isolate")]
mod isolate;
#[cfg(feature = "html")]
mod html_entities;
#[cfg(any(feature = "doc", feature = "ppt"))]
mod legacy;
#[cfg(feature = "pdf")]
mod pdf;
#[cfg(feature = "xlsx")]
mod xlsx;

#[cfg(feature = "isolate")]
pub use isolate::{parse_isolated, Isolation};

pub struct Parsed {
    pub text: String,
    /// Python class name, e.g. "TextParser".
    pub parser: &'static str,
}

#[derive(Debug, thiserror::Error)]
pub enum ParseError {
    /// Unusable file (binary, corrupt, encrypted, no parser yet). Caller warns and skips;
    /// binary is never ingested as text.
    #[error("{0}")]
    Skip(String),
    /// Recognized format whose cargo feature is off: a hard error (fails the cell), never a silent skip.
    #[error("`.{ext}` parsing requires the `{feature}` cargo feature (rebuild with --features {feature})")]
    FeatureDisabled { ext: String, feature: &'static str },
}

/// Worker output carries names as text; map back to the `&'static str`s the API promises.
#[cfg(feature = "isolate")]
fn intern_parser(n: &str) -> &'static str {
    ["TextParser", "PDFParser", "DOCXParser", "PPTXParser", "XLSXParser", "XLSParser", "XLSBParser", "ODSParser", "ODTParser", "ODPParser", "HTMLParser", "DOCParser", "PPTParser"]
        .into_iter()
        .find(|k| *k == n)
        .unwrap_or("UnknownParser")
}
#[cfg(feature = "isolate")]
fn intern_feature(n: &str) -> &'static str {
    ["pdf", "xlsx", "docx", "pptx", "odf", "doc", "ppt", "html"].into_iter().find(|k| *k == n).unwrap_or("all-parsers")
}

/// Files above this are skipped (PDF/OLE parsers hold the whole file plus expansions in memory).
const MAX_FILE_BYTES: u64 = 256 << 20;

/// Dispatch on lowercase extension, as Python does. A panic inside a third-party parser becomes a
/// `Skip`; NULs are stripped because Postgres TEXT rejects them (one poisoned file must not fail a cell).
pub fn parse(ext: &str, bytes: &[u8]) -> Result<Parsed, ParseError> {
    // ponytail: catch_unwind covers panics only, not stack overflow/OOM; subprocess isolation if hostile input is expected.
    let ext = ext.to_lowercase();
    let r = std::panic::catch_unwind(|| dispatch(&ext, bytes))
        .unwrap_or_else(|_| Err(ParseError::Skip(format!("parser for .{ext} panicked"))));
    r.map(|mut p| {
        p.text.retain(|c| c != '\0');
        p
    })
}

fn dispatch(ext: &str, bytes: &[u8]) -> Result<Parsed, ParseError> {
    macro_rules! arm {
        ($feat:literal, $name:literal, $call:expr) => {{
            #[cfg(feature = $feat)]
            {
                $call(bytes).map(|text| Parsed { text, parser: $name })
            }
            #[cfg(not(feature = $feat))]
            {
                Err(ParseError::FeatureDisabled { ext: ext.to_string(), feature: $feat })
            }
        }};
    }
    match ext {
        "pdf" => arm!("pdf", "PDFParser", pdf::parse),
        "xlsx" => arm!("xlsx", "XLSXParser", xlsx::parse),
        "docx" => arm!("docx", "DOCXParser", ooxml::docx),
        "pptx" => arm!("pptx", "PPTXParser", ooxml::pptx),
        "html" | "htm" => arm!("html", "HTMLParser", html::parse),
        "xls" => arm!("xlsx", "XLSParser", xlsx::xls),
        "xlsb" => arm!("xlsx", "XLSBParser", xlsx::xlsb),
        "ods" => arm!("xlsx", "ODSParser", xlsx::ods),
        "doc" => arm!("doc", "DOCParser", legacy::doc),
        "ppt" => arm!("ppt", "PPTParser", legacy::ppt),
        "odt" => arm!("odf", "ODTParser", |b| ooxml::odf(b, "ODT")),
        "odp" => arm!("odf", "ODPParser", |b| ooxml::odf(b, "ODP")),
        _ => parse_text(ext, bytes),
    }
}

fn parse_text(ext: &str, bytes: &[u8]) -> Result<Parsed, ParseError> {
    // ponytail: git's NUL heuristic; UTF-16 text gets skipped, add encoding sniffing if needed.
    if bytes.iter().take(8192).any(|&b| b == 0) {
        return Err(ParseError::Skip(format!(
            "binary content; no parser for .{ext}"
        )));
    }
    Ok(Parsed {
        text: String::from_utf8_lossy(bytes).into_owned(),
        parser: "TextParser",
    })
}

/// Parsing counterpart of core's `FilesSource`.
pub struct ParsedFilesSource {
    cfg: FilesSourceConfig,
    #[cfg(feature = "isolate")]
    isolation: Option<Isolation>,
}

impl ParsedFilesSource {
    pub fn new(cfg: FilesSourceConfig) -> Self {
        Self {
            cfg,
            #[cfg(feature = "isolate")]
            isolation: None,
        }
    }

    /// Parse binary formats in a worker process (see `isolate` module). Build the worker with the
    /// same parser features: `cargo build --features all-parsers,isolate`.
    #[cfg(feature = "isolate")]
    pub fn with_isolation(mut self, iso: Isolation) -> Self {
        self.isolation = Some(iso);
        self
    }

    fn parse_one(&self, ext: &str, bytes: &[u8]) -> Result<Parsed, ParseError> {
        #[cfg(feature = "isolate")]
        if let Some(iso) = &self.isolation {
            if isolate::needs_isolation(&ext.to_lowercase()) {
                return parse_isolated(ext, bytes, iso);
            }
        }
        parse(ext, bytes)
    }

    /// Glob, sort, parse. Skipped files are warned about and omitted.
    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut paths: Vec<PathBuf> = glob::glob(&self.cfg.glob)
            .with_context(|| format!("invalid glob {:?}", self.cfg.glob))?
            .filter_map(std::result::Result::ok)
            .collect();
        if paths.is_empty() {
            return Err(anyhow!("no files matched glob: {}", self.cfg.glob));
        }
        paths.sort();

        let mut out = Vec::with_capacity(paths.len());
        for p in paths {
            let meta = std::fs::metadata(&p).with_context(|| format!("stat {}", p.display()))?;
            if !meta.is_file() || meta.len() > MAX_FILE_BYTES {
                tracing::warn!(target: "chunkshop_parsers", "skipping {}: not a regular file or over {MAX_FILE_BYTES} bytes", p.display());
                continue;
            }
            let bytes = std::fs::read(&p).with_context(|| format!("reading {}", p.display()))?;
            let ext = p
                .extension()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_lowercase();
            let parsed = match self.parse_one(&ext, &bytes) {
                Ok(x) => x,
                Err(ParseError::Skip(reason)) => {
                    tracing::warn!(target: "chunkshop_parsers", "skipping {}: {reason}", p.display());
                    continue;
                }
                Err(e @ ParseError::FeatureDisabled { .. }) => {
                    return Err(anyhow!("{}: {e}", p.display()));
                }
            };
            out.push(Document {
                id: self.id_for(&p)?,
                content: parsed.text,
                title: p.file_name().and_then(|s| s.to_str()).map(String::from),
                metadata: json!({
                    "source_path": p.display().to_string(),
                    "parser": parsed.parser,
                }),
                fingerprint: None,
            });
        }
        Ok(out)
    }

    // Same semantics as core's private `FilesSource::id_for`.
    fn id_for(&self, path: &Path) -> Result<String> {
        match self.cfg.id_from.as_str() {
            "path" => Ok(path.display().to_string()),
            "stem" => path
                .file_stem()
                .and_then(|s| s.to_str())
                .map(String::from)
                .ok_or_else(|| anyhow!("file has no stem: {}", path.display())),
            "sha1" => {
                let mut h = Sha1::new();
                h.update(path.display().to_string().as_bytes());
                Ok(format!("{:x}", h.finalize()))
            }
            other => Err(anyhow!("unknown id_from: {other}")),
        }
    }
}
