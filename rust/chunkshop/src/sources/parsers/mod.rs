//! Per-extension file derivers. Mirrors `python/src/chunkshop/sources/parsers/`.
//! `Deriver::derive(path) -> ParseResult` extracts a file's content plus any
//! metadata the extraction produced. `FilesSource` wraps the result into a
//! `Document`. Unlike Python's `FileParser`/`Deriver` split (kept separate
//! there for backward compatibility with pre-1.0 third-party parsers), Rust
//! has no such compatibility surface to preserve — one trait, from day one.

use std::path::{Path, PathBuf};

use serde_json::{Map, Value};
use thiserror::Error;

// `registry` (extension -> Deriver dispatch) and `text` (the always-available
// TextDeriver) are not `media`-gated — every text-only YAML must keep
// working with no media feature involved at all.
pub mod registry;
pub mod text;

// `html` is Task 2's, `pdf` is Task 3's, `xlsx` is Task 4's, `docx` is
// Task 5's — all four declared now. `#[cfg(feature = "media")]` only
// skips a `pub mod X;` line when the feature is off, but Rust still
// requires `X.rs` to exist at name-resolution time whenever the feature
// is on, so a module is declared only by the task that creates its file.
#[cfg(feature = "media")]
pub mod docx;
#[cfg(feature = "media")]
pub mod html;
#[cfg(feature = "media")]
pub mod pdf;
#[cfg(feature = "media")]
pub mod xlsx;

/// What a deriver returns: extracted text plus whatever metadata the
/// extraction produced (dimensions, sheet names, model provenance, ...).
#[derive(Debug, Clone, PartialEq)]
pub struct ParseResult {
    pub text: String,
    pub metadata: Map<String, Value>,
    /// Evidence that this file DEMONSTRABLY held content the deriver failed
    /// to turn into text — a human phrase naming what it found
    /// (`"1 page(s)"`). Mirrors Python's `ParseResult.had_content`.
    ///
    /// This is the F5 signal, and it is deliberately an AFFIRMATIVE CLAIM by
    /// the deriver rather than something `FilesSource` infers from an empty
    /// `text`. The two are not the same fact, and conflating them is the
    /// regression this field exists to make impossible: a zero-byte or
    /// whitespace-only `.md` is LEGITIMATELY empty — bytes in, no text out,
    /// nothing lost — while a PDF with pages and no characters is a failed
    /// extraction. `TextDeriver` has no notion of "content units" at all and
    /// therefore can never set this, so no amount of empty text on the plain
    /// path can reach the loud branch.
    ///
    /// A deriver sets it only when it both (a) counted at least one content
    /// unit it knows how to read (a PDF page, a DOCX table, an embedded HTML
    /// element) and (b) produced no characters. `None` — the default, and
    /// the right answer for any deriver with no honest way to tell the two
    /// cases apart — makes no claim either way.
    pub had_content: Option<String>,
}

impl ParseResult {
    pub fn new(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            metadata: Map::new(),
            had_content: None,
        }
    }

    pub fn with_metadata(text: impl Into<String>, metadata: Map<String, Value>) -> Self {
        Self {
            text: text.into(),
            metadata,
            had_content: None,
        }
    }

    /// The F5 constructor: text came out blank, but the file held
    /// `evidence` worth of content this deriver knows how to read.
    pub fn unextractable(text: impl Into<String>, evidence: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            metadata: Map::new(),
            had_content: Some(evidence.into()),
        }
    }
}

/// Where to send a user whose corpus genuinely is scanned images. chunkshop
/// does not OCR and does not intend to; the honest remedy is preprocessing.
/// Byte-identical to Python's `_PREPROCESS_HINT` in `sources/files.py`.
const PREPROCESS_HINT: &str = "preprocess it to markdown first (e.g. yonk-doctools, which does \
     PDF/DOCX -> markdown with image captioning) and ingest that output";

/// Mirrors Python's `EmptyDerivationError` — a recognized file that produced
/// no text. Raised instead of silently indexing an empty document.
///
/// Two distinct failures share this one type, and they need different advice,
/// so `Display` branches on `had_content` exactly as Python's
/// `files::_empty_message` does. A DERIVER that produced nothing is a
/// rung/config problem. A PARSER that read real content units and produced
/// nothing is spec defect F5's case — a scanned page, an image-only page —
/// and the user needs to be told what the file actually contained and that
/// preprocessing, not a config flag, is the real fix.
#[derive(Debug)]
pub struct EmptyDerivationError {
    pub path: PathBuf,
    pub parser: String,
    /// `Some(evidence)` for the F5 case; `None` for a derive-path failure.
    pub had_content: Option<String>,
}

impl std::fmt::Display for EmptyDerivationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match &self.had_content {
            Some(had) => write!(
                f,
                "{}: {} read {} but extracted no text. The file has content \
                 that produced no characters — typically a scanned PDF or an \
                 image-only page with no text layer. chunkshop does not OCR: \
                 {}. To continue past files like this instead, set \
                 source.on_empty='skip' (or 'index' to index it as an empty \
                 document anyway).",
                self.path.display(),
                self.parser,
                had,
                PREPROCESS_HINT,
            ),
            None => write!(
                f,
                "{} produced no text via {}",
                self.path.display(),
                self.parser
            ),
        }
    }
}

impl std::error::Error for EmptyDerivationError {}

/// Mirrors Python's `RuntimeError` "install the extra" convention (NN-6): a
/// known format was matched but the binary wasn't built with the feature
/// that provides it.
#[derive(Debug, Error)]
#[error("{format} parsing requires the `{feature}` Cargo feature; rebuild with --features {feature}")]
pub struct MissingFeatureError {
    pub format: &'static str,
    pub feature: &'static str,
}

/// A file recognized but not yet parseable — distinct from `MissingFeatureError`
/// (which names a feature flag that WOULD fix it). Used for PPTX (Task 2):
/// there is no Rust crate committed to yet, so this is not a build-config fix.
#[derive(Debug, Error)]
#[error("{format} parsing is not yet implemented in the Rust port (see docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md); falling back to text would silently corrupt binary content, so this file was refused instead")]
pub struct UnsupportedFormatError {
    pub format: &'static str,
}

pub trait Deriver: Send + Sync {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult>;

    /// The value `FilesSource` stamps into `metadata["parser"]`, and the
    /// name an `EmptyDerivationError` reports.
    ///
    /// Python writes `parser.__class__.__name__` (the metadata-assembly
    /// block in `files.py`), so this must return the **Python** class name
    /// for the same format — `"PDFParser"`, not `"PdfDeriver"`. The two
    /// ports name their types differently, so `std::any::type_name` would
    /// produce a value that is merely *present* rather than *equal*: a
    /// subtler parity defect than the missing key it would be replacing.
    ///
    /// Deliberately REQUIRED, with no trait default. A default would hand
    /// every future deriver a plausible-looking wrong name for free, and
    /// this key is provenance that users grep and filter on — a wrong
    /// value is worse than a compile error. Requiring it forces whoever
    /// adds the next deriver to go look up what Python calls it.
    fn parser_name(&self) -> &'static str;
}

/// Derivers that participate in the derivation cache (Task 7) expose their
/// own `(rung, model_id, model_version, params)` — the exact tuple both
/// `derivers::provenance::derivation_hash` and `stamp_provenance` are
/// called with, so the cache key and the provenance stamp can never
/// silently disagree (Global Constraints). Mirrors Python's duck-typed
/// `getattr(parser, "derivation_hash", None)` callback in `files.py`.
/// Implemented by `ImageMetaDeriver` (Task 9) and `ClipTagsDeriver` (a
/// later task).
pub trait CacheableDeriver: Deriver {
    fn cache_key_parts(&self) -> (String, String, String, Value);

    /// The `engine` folded into the derivation hash and stamped into
    /// `derived.engine`.
    ///
    /// Defaults to `"rust"`, which is right for every IN-PROCESS rung: the
    /// derivation really was performed by this port, and the field exists
    /// so a Python-derived and a Rust-derived value can share one cache
    /// directory without masquerading as each other.
    ///
    /// It is overridable because rung 5 is not in-process. `RossDeriver`
    /// shells out to the same `ross` binary from BOTH ports, so stamping
    /// the host language there would claim a derivation neither port
    /// performed — and would split one genuinely shared cache entry in two.
    /// `files.rs` reads this rather than hardcoding `"rust"` at the call
    /// site, which is what it used to do.
    fn engine(&self) -> &'static str {
        "rust"
    }

    /// Per-document material this deriver consumes beyond its bytes.
    ///
    /// THE CACHE-SOUNDNESS INVARIANT. A deriver participating in the
    /// derivation cache must be a pure function of its file's CONTENT plus
    /// everything it declares in the cache key. The key is
    /// `H(content_hash, derivation_hash, cache_key_extra)` — the content
    /// bytes, the (rung, model, version, engine, params) tuple, and
    /// whatever per-document material the deriver names here. Anything
    /// else a deriver reads — the filename, the parent directory, the
    /// mtime, an environment variable — is invisible to the key, so two
    /// files that agree on the key are served each other's output. That is
    /// not hypothetical: it shipped as a silent data-corruption bug,
    /// because `ImageMetaDeriver` derives its output text from the
    /// filename while the key contained no path at all.
    ///
    /// This is the escape hatch, not a licence. A deriver that consumes
    /// per-document material outside its own bytes returns a string
    /// capturing exactly that material — and only what it actually
    /// consumes under its CURRENT config — so it stops colliding. The
    /// default `None` is the correct answer for any deriver that is a pure
    /// function of content: it keeps full content-addressed sharing, so
    /// two copies of the same PDF still derive once, which is the property
    /// worth preserving. A deriver that wraps another must DELEGATE, or it
    /// silently drops the inner deriver's contribution.
    fn cache_key_extra(&self, _path: &Path) -> Option<String> {
        None
    }
}

/// Mirrors Python's `AmbiguousDeriverError` (Task 9 Correction 2). Two
/// different `derive:` keys in `FilesSourceConfig` both claim the SAME
/// file extension. Python's `cfg.derive` dict preserves YAML insertion
/// order (so the *later* key silently wins); Rust's is a `BTreeMap`
/// (lexicographic key order) — the two languages can pick OPPOSITE
/// winners for the byte-identical config. Rather than let that silently
/// diverge, both languages reject the ambiguity outright with a named
/// error citing the extension and both colliding keys.
#[derive(Debug, Clone, Error)]
#[error(
    "extension '{ext}' is reachable from two different `derive` keys: \
     '{first_key}' and '{second_key}'. Each extension must be routed by \
     exactly one `derive` key."
)]
pub struct AmbiguousDeriverError {
    pub ext: String,
    pub first_key: String,
    pub second_key: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_result_new_has_empty_metadata() {
        let r = ParseResult::new("hello");
        assert_eq!(r.text, "hello");
        assert!(r.metadata.is_empty());
    }

    #[test]
    fn parse_result_with_metadata_holds_both() {
        let mut md = Map::new();
        md.insert("width".to_string(), Value::from(8));
        let r = ParseResult::with_metadata("hi", md.clone());
        assert_eq!(r.text, "hi");
        assert_eq!(r.metadata, md);
    }

    #[test]
    fn empty_derivation_error_names_path_and_parser() {
        let e = EmptyDerivationError {
            path: PathBuf::from("scanned.pdf"),
            parser: "PdfParser".to_string(),
            had_content: None,
        };
        let msg = e.to_string();
        assert!(msg.contains("scanned.pdf"), "{msg}");
        assert!(msg.contains("PdfParser"), "{msg}");
    }

    /// F5: the `had_content` branch says what the file HELD and what to do
    /// about it. Kept in lockstep with Python's `files::_empty_message`.
    #[test]
    fn empty_derivation_error_with_evidence_is_actionable() {
        let e = EmptyDerivationError {
            path: PathBuf::from("/corpus/scanned.pdf"),
            parser: "PDFParser".to_string(),
            had_content: Some("3 page(s)".to_string()),
        };
        let msg = e.to_string();
        for needle in [
            "/corpus/scanned.pdf",
            "PDFParser",
            "3 page(s)",
            "extracted no text",
            "no text layer",
            "does not OCR",
            "yonk-doctools",
            "source.on_empty='skip'",
        ] {
            assert!(msg.contains(needle), "missing {needle:?} in: {msg}");
        }
        // The line-continuation escapes must not have eaten the spaces.
        assert!(!msg.contains("  "), "double space in: {msg}");
    }

    #[test]
    fn parse_result_constructors_make_no_claim_by_default() {
        assert!(ParseResult::new("x").had_content.is_none());
        assert!(ParseResult::with_metadata("x", Map::new())
            .had_content
            .is_none());
        assert_eq!(
            ParseResult::unextractable("", "2 table(s)")
                .had_content
                .as_deref(),
            Some("2 table(s)")
        );
    }
}
