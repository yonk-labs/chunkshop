//! YAML config parsing.
//!
//! Accepts the same YAML shape as the Python reference implementation, but
//! only the minimal subset: files source, sentence_aware chunker, fastembed
//! embedder, pgvector target. Unknown fields are tolerated at the top level
//! (e.g. `extractor:`, `framer:`, `runtime:` extras) so Python-authored YAMLs
//! parse without edits — per-section structs use serde's default untagged
//! behavior with explicit fields.

use std::path::Path;

use anyhow::{anyhow, Context, Result};
use regex::Regex;
use serde::Deserialize;

const ALLOWED_PROMOTE_TYPES: &[&str] = &[
    "text",
    "text[]",
    "int",
    "bigint",
    "boolean",
    "jsonb",
    "timestamptz",
    "date",
];

/// Allowlist regex for `ClickhouseTargetConfig::engine`. Hardening relative to
/// Python (which interpolates the engine string raw — see
/// python/src/chunkshop/config.py:542). Accepts:
///   - `MergeTree` / `MergeTree()`
///   - `ReplacingMergeTree(<single_ident>)` (the `created_at` dedup column)
///   - Any of the above optionally followed by ` ORDER BY <expr>`
///
/// Rejects engines outside this whitelist (Replicated*, Distributed, Memory,
/// engines with embedded SQL, etc.) — those need explicit user request and a
/// separate brief.
const CLICKHOUSE_ENGINE_RE: &str = r"^(MergeTree(\(\))?|ReplacingMergeTree\(\w+\))( ORDER BY .+)?$";

/// One promoted jsonb path → typed Postgres column. Mirrors Python's
/// `chunkshop.config.PromoteColumn`. The `path` is dot-separated; each segment
/// must match `^[A-Za-z_][A-Za-z0-9_]*$`. The `type_` must be in
/// `ALLOWED_PROMOTE_TYPES` — this is SQL-injection-prevention by allowlist:
/// `_ensure_promote_columns` interpolates the type as a literal into DDL.
#[derive(Debug, Clone)]
pub struct PromoteColumn {
    pub path: String,
    pub type_: String,
}

impl PromoteColumn {
    /// Postgres column identifier — dots → double-underscore, lowercased.
    /// Mirrors Python's `PromoteColumn.column_name`.
    pub fn column_name(&self) -> String {
        self.path.replace('.', "__").to_lowercase()
    }

    fn validate_path(path: &str) -> std::result::Result<(), String> {
        if path.is_empty() {
            return Err("path must not be empty".into());
        }
        let seg_re = Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*$").unwrap();
        for seg in path.split('.') {
            if !seg_re.is_match(seg) {
                return Err(format!(
                    "path segments must match ^[A-Za-z_][A-Za-z0-9_]*$ separated by '.', got {path:?}"
                ));
            }
        }
        Ok(())
    }

    fn validate_type(t: &str) -> std::result::Result<(), String> {
        if !ALLOWED_PROMOTE_TYPES.contains(&t) {
            return Err(format!(
                "promote_metadata type must be one of {ALLOWED_PROMOTE_TYPES:?}, got {t:?}"
            ));
        }
        Ok(())
    }
}

impl<'de> serde::Deserialize<'de> for PromoteColumn {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        #[derive(serde::Deserialize)]
        struct Raw {
            path: String,
            #[serde(rename = "type")]
            type_: String,
        }
        let r = Raw::deserialize(d)?;
        Self::validate_path(&r.path).map_err(serde::de::Error::custom)?;
        Self::validate_type(&r.type_).map_err(serde::de::Error::custom)?;
        Ok(Self {
            path: r.path,
            type_: r.type_,
        })
    }
}

/// One YAML = one cell. Matches `python/src/chunkshop/config.py::CellConfig`.
#[derive(Debug, Clone, Deserialize)]
pub struct CellConfig {
    pub cell_name: String,
    pub source: SourceConfig,
    pub chunker: ChunkerConfig,
    pub embedder: EmbedderConfig,
    pub target: TargetConfig,
    #[serde(default)]
    pub runtime: RuntimeConfig,
    #[serde(default)]
    pub framer: FramerConfig,
    #[serde(default)]
    pub extractor: ExtractorConfig,
}

/// Discriminated union over extractor types. Mirrors Python's `ExtractorConfig`.
/// Tagged on `type`. Default = `None` (the no-op extractor — equivalent to
/// "no extractor stage" in pre-extractor YAMLs).
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ExtractorConfig {
    None(NoneExtractorConfig),
    Composite(CompositeExtractorConfig),
    RakeKeywords(RakeKeywordsExtractorConfig),
    LangDetect(LangDetectExtractorConfig),
    KeybertPhrases(KeybertPhrasesExtractorConfig),
    SpacyEntities(SpacyEntitiesExtractorConfig),
}

impl Default for ExtractorConfig {
    fn default() -> Self {
        ExtractorConfig::None(NoneExtractorConfig::default())
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct NoneExtractorConfig {}

#[derive(Debug, Clone, Deserialize)]
pub struct CompositeExtractorConfig {
    #[serde(default)]
    pub extractors: Vec<ExtractorConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RakeKeywordsExtractorConfig {
    #[serde(default = "default_rake_top_k")]
    pub top_k: usize,
    #[serde(default = "default_rake_min_chars")]
    pub min_chars: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LangDetectExtractorConfig {
    #[serde(default = "default_lang_backend")]
    pub backend: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct KeybertPhrasesExtractorConfig {
    #[serde(default = "default_keybert_top_k")]
    pub top_k: usize,
    #[serde(default = "default_keybert_model")]
    pub model_name: String,
    #[serde(default = "default_keybert_ngram")]
    pub keyphrase_ngram_range: (usize, usize),
}

#[derive(Debug, Clone, Deserialize)]
pub struct SpacyEntitiesExtractorConfig {
    #[serde(default = "default_spacy_model")]
    pub model: String,
    #[serde(default = "default_spacy_whitelist")]
    pub label_whitelist: Vec<String>,
}

fn default_rake_top_k() -> usize { 10 }
fn default_rake_min_chars() -> usize { 3 }
fn default_lang_backend() -> String { "langdetect".to_string() }
fn default_keybert_top_k() -> usize { 10 }
fn default_keybert_model() -> String { "all-MiniLM-L6-v2".to_string() }
fn default_keybert_ngram() -> (usize, usize) { (1, 2) }
fn default_spacy_model() -> String { "en_core_web_sm".to_string() }
fn default_spacy_whitelist() -> Vec<String> {
    vec!["ORG", "PERSON", "GPE", "DATE", "LAW"]
        .into_iter()
        .map(String::from)
        .collect()
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FramerConfig {
    Identity(IdentityFramerConfig),
    HeadingBoundary(HeadingBoundaryFramerConfig),
    RegexBoundary(RegexBoundaryFramerConfig),
    Jsonpath(JsonPathFramerConfig),
}

impl Default for FramerConfig {
    fn default() -> Self {
        FramerConfig::Identity(IdentityFramerConfig {})
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct IdentityFramerConfig {}

#[derive(Debug, Clone, Deserialize)]
pub struct HeadingBoundaryFramerConfig {
    #[serde(default = "default_heading_pattern")]
    pub pattern: String,
    #[serde(default = "default_true")]
    pub title_from_heading: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RegexBoundaryFramerConfig {
    pub split_pattern: String,
    #[serde(default)]
    pub title_pattern: Option<String>,
    #[serde(default = "default_true")]
    pub body_starts_with_match: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct JsonPathFramerConfig {
    pub row_path: String,
    #[serde(default)]
    pub title_path: Option<String>,
    #[serde(default = "default_jsonpath_body")]
    pub body_path: String,
}

fn default_heading_pattern() -> String {
    r"^#+\s".to_string()
}
fn default_true() -> bool {
    true
}
fn default_jsonpath_body() -> String {
    "$".to_string()
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SourceConfig {
    Files(FilesSourceConfig),
    JsonCorpus(JsonCorpusSourceConfig),
    PgTable(PgTableSourceConfig),
    MariadbTable(MariadbTableSourceConfig),
    SqliteTable(SqliteTableSourceConfig),
    Http(HttpSourceConfig),
    S3(S3SourceConfig),
    ClickhouseTable(ClickhouseTableSourceConfig),
    /// Library/embedded mode — no automatic iteration. The host application
    /// drives ingestion via `chunkshop::Pipeline::from_yaml(...)` and calls
    /// `pipeline.ingest_text(doc_id, text, metadata)` per document.
    /// `Runner::run_cell` rejects this variant; only `Pipeline` accepts it.
    Inline(InlineSourceConfig),
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct InlineSourceConfig {}

#[derive(Debug, Clone, Deserialize)]
pub struct FilesSourceConfig {
    pub glob: String,
    #[serde(default = "default_id_from")]
    pub id_from: String,
    #[serde(default = "default_encoding")]
    pub encoding: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct JsonCorpusSourceConfig {
    pub path: String,
    #[serde(default = "default_documents_key")]
    pub documents_key: String,
    #[serde(default = "default_id_field")]
    pub id_field: String,
    #[serde(default = "default_content_field")]
    pub content_field: String,
    #[serde(default = "default_title_field")]
    pub title_field: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PgTableSourceConfig {
    pub dsn_env: String,
    #[serde(rename = "schema")]
    pub schema_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Mirrors
    /// Python's `pg_table.py` which interpolates this verbatim. NOT validated;
    /// don't expose this field to untrusted YAML authors.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    /// Extra columns to pull alongside id/content/title and put into each
    /// Document's metadata. Pair with `target.promote_metadata` to surface
    /// specific keys as typed columns in the target table.
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct MariadbTableSourceConfig {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Same
    /// contract as PgTableSourceConfig.where_clause — NOT validated.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}

/// SQLite source. Mirrors `python/src/chunkshop/sources/sqlite_table.py`.
/// `database` is validated as a non-empty ident at config-load (loose parity
/// with Postgres) but ignored at runtime — SQLite has no schema namespace.
#[derive(Debug, Clone, Deserialize)]
pub struct SqliteTableSourceConfig {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Same
    /// contract as PgTableSourceConfig.where_clause — NOT validated.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}

/// ClickHouse source. Mirrors `python/src/chunkshop/sources/clickhouse_table.py`.
#[derive(Debug, Clone, Deserialize)]
pub struct ClickhouseTableSourceConfig {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Mirrors
    /// Python's `clickhouse_table.py` which interpolates this verbatim. NOT
    /// validated; don't expose this field to untrusted YAML authors.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HttpSourceConfig {
    #[serde(default)]
    pub urls: Vec<String>,
    #[serde(default)]
    pub sitemap: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct S3SourceConfig {
    pub bucket: String,
    #[serde(default)]
    pub prefix: String,
    /// Optional S3-compatible endpoint URL (minio, R2, custom). When None,
    /// `object_store` resolves the standard AWS S3 endpoint per the
    /// credential's region.
    #[serde(default)]
    pub endpoint_url: Option<String>,
}

fn default_id_from() -> String {
    "stem".to_string()
}

fn default_encoding() -> String {
    "utf-8".to_string()
}

fn default_documents_key() -> String {
    "documents".to_string()
}
fn default_id_field() -> String {
    "id".to_string()
}
fn default_content_field() -> String {
    "content".to_string()
}
fn default_title_field() -> Option<String> {
    Some("title".to_string())
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChunkerConfig {
    SentenceAware(SentenceAwareChunkerConfig),
    Hierarchy(HierarchyChunkerConfig),
    FixedOverlap(FixedOverlapChunkerConfig),
    NeighborExpand(NeighborExpandChunkerConfig),
    Semantic(SemanticChunkerConfig),
    SummaryEmbed(SummaryEmbedChunkerConfig),
    HierarchicalSummary(HierarchicalSummaryChunkerConfig),
}

impl ChunkerConfig {
    /// Resolve the effective `max_chars` ceiling for this chunker. Wrappers
    /// (`neighbor_expand`, `summary_embed`, `hierarchical_summary`) fall back
    /// to `base.effective_max_chars()` when no explicit override is set.
    /// `fixed_overlap` returns `None` unless the user opted in via `max_chars`.
    /// Mirrors Python's `ChunkerConfig.effective_max_chars` resolver.
    /// Brief SC-003.
    pub fn effective_max_chars(&self) -> Option<usize> {
        match self {
            ChunkerConfig::SentenceAware(c) => Some(c.max_chars),
            ChunkerConfig::Hierarchy(c) => Some(c.max_chars),
            ChunkerConfig::FixedOverlap(c) => c.max_chars,
            ChunkerConfig::Semantic(c) => Some(c.max_chunk_chars),
            ChunkerConfig::NeighborExpand(c) => {
                c.max_chars.or_else(|| c.base.effective_max_chars())
            }
            ChunkerConfig::SummaryEmbed(c) => {
                c.max_chars.or_else(|| c.base.effective_max_chars())
            }
            ChunkerConfig::HierarchicalSummary(c) => {
                c.max_chars.or_else(|| c.base.effective_max_chars())
            }
        }
    }

    /// Borrow the optional `if_oversize` fallback chunker config. Returns
    /// `None` for chunkers that haven't opted in. Brief SC-001.
    pub fn if_oversize(&self) -> Option<&ChunkerConfig> {
        match self {
            ChunkerConfig::SentenceAware(c) => c.if_oversize.as_deref(),
            ChunkerConfig::Hierarchy(c) => c.if_oversize.as_deref(),
            ChunkerConfig::FixedOverlap(c) => c.if_oversize.as_deref(),
            ChunkerConfig::Semantic(c) => c.if_oversize.as_deref(),
            ChunkerConfig::NeighborExpand(c) => c.if_oversize.as_deref(),
            ChunkerConfig::SummaryEmbed(c) => c.if_oversize.as_deref(),
            ChunkerConfig::HierarchicalSummary(c) => c.if_oversize.as_deref(),
        }
    }

    /// Stable lower-snake-case discriminator string for logs/error messages.
    pub fn type_name(&self) -> &'static str {
        match self {
            ChunkerConfig::SentenceAware(_) => "sentence_aware",
            ChunkerConfig::Hierarchy(_) => "hierarchy",
            ChunkerConfig::FixedOverlap(_) => "fixed_overlap",
            ChunkerConfig::NeighborExpand(_) => "neighbor_expand",
            ChunkerConfig::Semantic(_) => "semantic",
            ChunkerConfig::SummaryEmbed(_) => "summary_embed",
            ChunkerConfig::HierarchicalSummary(_) => "hierarchical_summary",
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct SentenceAwareChunkerConfig {
    #[serde(default = "default_doc_type")]
    pub doc_type: String,
    #[serde(default = "default_max_chars")]
    pub max_chars: usize,
    #[serde(default = "default_min_chars")]
    pub min_chars: usize,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HierarchyChunkerConfig {
    #[serde(default = "default_prefix_heading")]
    pub prefix_heading: bool,
    #[serde(default = "default_min_section_chars")]
    pub min_section_chars: usize,
    #[serde(default = "default_max_chars")]
    pub max_chars: usize,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FixedOverlapChunkerConfig {
    #[serde(default = "default_window_words")]
    pub window_words: usize,
    #[serde(default = "default_step_words")]
    pub step_words: usize,
    /// Optional post-hoc char ceiling for emitted chunks. Mirrors Python's
    /// `FixedOverlapChunker.max_chars` added in 0.3.2 (Brief SC-002). When
    /// `None`, behavior is unchanged from 0.3.1 (word-only window). When set,
    /// the chunker pairs with `if_oversize` to fall back over chunks that
    /// exceed this ceiling.
    #[serde(default)]
    pub max_chars: Option<usize>,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NeighborExpandChunkerConfig {
    pub base: Box<ChunkerConfig>,
    #[serde(default = "default_neighbor_window")]
    pub window: usize,
    /// Explicit char ceiling override. When `None`, the wrapper inherits
    /// `base.effective_max_chars()` (Brief SC-003).
    #[serde(default)]
    pub max_chars: Option<usize>,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SemanticChunkerConfig {
    #[serde(default = "default_boundary_model")]
    pub boundary_model: String,
    #[serde(default = "default_breakpoint_percentile")]
    pub breakpoint_percentile: u32,
    #[serde(default = "default_min_sents_per_chunk")]
    pub min_sentences_per_chunk: usize,
    #[serde(default = "default_max_chunk_chars")]
    pub max_chunk_chars: usize,
    #[serde(default = "default_sentence_splitter")]
    pub sentence_splitter: String,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

/// Discriminated union over summarizer modes. Mirrors Python's `SummarizerConfig`.
/// Tagged on `mode` (matches the Python YAML).
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum SummarizerConfig {
    External(ExternalSummarizerConfig),
    Callable(CallableSummarizerConfig),
    Passthrough(PassthroughSummarizerConfig),
}

impl SummarizerConfig {
    /// One of `"external"`, `"callable"`, `"passthrough"` — the value chunkshop
    /// stamps into `metadata.summarizer` for traceability.
    pub fn mode_str(&self) -> &'static str {
        match self {
            SummarizerConfig::External(_) => "external",
            SummarizerConfig::Callable(_) => "callable",
            SummarizerConfig::Passthrough(_) => "passthrough",
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ExternalSummarizerConfig {
    #[serde(default = "default_external_field")]
    pub field: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CallableSummarizerConfig {
    pub module: String,
    #[serde(default = "default_callable_function")]
    pub function: String,
    #[serde(default)]
    pub kwargs: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct PassthroughSummarizerConfig {}

fn default_external_field() -> String {
    "summary".to_string()
}
fn default_callable_function() -> String {
    "summarize".to_string()
}

/// Discriminated union over grouping strategies for HierarchicalSummaryChunker.
/// Mirrors Python's `GroupingConfig`. Tagged on `strategy`.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "strategy", rename_all = "snake_case")]
pub enum GroupingConfig {
    FixedN(FixedNGroupingConfig),
    WordBudget(WordBudgetGroupingConfig),
    SectionAware(SectionAwareGroupingConfig),
}

impl Default for GroupingConfig {
    fn default() -> Self {
        GroupingConfig::FixedN(FixedNGroupingConfig::default())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct FixedNGroupingConfig {
    #[serde(default = "default_fixed_n")]
    pub n: usize,
}

impl Default for FixedNGroupingConfig {
    fn default() -> Self {
        Self { n: default_fixed_n() }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct WordBudgetGroupingConfig {
    #[serde(default = "default_word_budget")]
    pub max_words: usize,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct SectionAwareGroupingConfig {}

fn default_fixed_n() -> usize {
    5
}
fn default_word_budget() -> usize {
    2000
}

#[derive(Debug, Clone, Deserialize)]
pub struct SummaryEmbedChunkerConfig {
    pub base: Box<ChunkerConfig>,
    pub summarizer: SummarizerConfig,
    /// Explicit char ceiling override. When `None`, the wrapper inherits
    /// `base.effective_max_chars()` (Brief SC-003).
    #[serde(default)]
    pub max_chars: Option<usize>,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HierarchicalSummaryChunkerConfig {
    pub base: Box<ChunkerConfig>,
    pub summarizer: SummarizerConfig,
    #[serde(default)]
    pub grouping: GroupingConfig,
    /// Explicit char ceiling override. When `None`, the wrapper inherits
    /// `base.effective_max_chars()` (Brief SC-003). Only fine rows are
    /// checked; coarse rows are exempt by design (Brief SC-005).
    #[serde(default)]
    pub max_chars: Option<usize>,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

fn default_window_words() -> usize {
    300
}
fn default_step_words() -> usize {
    150
}
fn default_neighbor_window() -> usize {
    1
}
fn default_boundary_model() -> String {
    "sentence-transformers/all-MiniLM-L6-v2-int8".to_string()
}
fn default_breakpoint_percentile() -> u32 {
    95
}
fn default_min_sents_per_chunk() -> usize {
    3
}
fn default_max_chunk_chars() -> usize {
    2000
}
fn default_sentence_splitter() -> String {
    "naive".to_string()
}

fn default_doc_type() -> String {
    "prose".to_string()
}
fn default_max_chars() -> usize {
    2000
}
fn default_min_chars() -> usize {
    200
}
fn default_prefix_heading() -> bool {
    true
}
fn default_min_section_chars() -> usize {
    100
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum EmbedderConfig {
    Fastembed(FastembedEmbedderConfig),
}

#[derive(Debug, Clone, Deserialize)]
pub struct FastembedEmbedderConfig {
    pub model_name: String,
    pub dim: usize,
    #[serde(default = "default_batch_size")]
    pub batch_size: usize,
    #[serde(default)]
    pub threads: Option<usize>,

    // YAML-driven HF pointer ("BYO embedder"). When `hf_repo` is set, the
    // Rust dispatch routes through the user-defined ONNX path with these
    // values at runtime — no `user_defined_source` edit, no rebuild
    // required. When NOT set, dispatch falls back to the registry
    // (resolve_model_name + user_defined_source for known names).
    #[serde(default)]
    pub hf_repo: Option<String>,
    #[serde(default)]
    pub onnx_path: Option<String>,
    #[serde(default = "default_pooling")]
    pub pooling: String, // "cls" | "mean"
    #[serde(default = "default_additional_files")]
    pub additional_files: Vec<String>,
}

fn default_batch_size() -> usize {
    64
}

fn default_pooling() -> String {
    "cls".to_string()
}

fn default_additional_files() -> Vec<String> {
    vec![
        "tokenizer.json".to_string(),
        "tokenizer_config.json".to_string(),
        "special_tokens_map.json".to_string(),
        "config.json".to_string(),
    ]
}

impl FastembedEmbedderConfig {
    /// True when YAML opted into BYO mode (both hf_repo + onnx_path set).
    pub fn is_byo(&self) -> bool {
        self.hf_repo.is_some() && self.onnx_path.is_some()
    }

    /// Validate field pairing: `hf_repo` and `onnx_path` go together. Called
    /// post-deserialize alongside `validate_ident` in `load_config`.
    pub fn validate(&self) -> Result<()> {
        if self.hf_repo.is_some() != self.onnx_path.is_some() {
            return Err(anyhow!(
                "embedder.hf_repo and embedder.onnx_path must be set together \
                 (BYO mode) or both omitted (registry mode)."
            ));
        }
        if self.hf_repo.is_some() && !matches!(self.pooling.as_str(), "cls" | "mean") {
            return Err(anyhow!(
                "embedder.pooling must be 'cls' or 'mean' for BYO embedders, got {:?}",
                self.pooling
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TargetConfig {
    Postgres(PostgresTargetConfig),
    Mariadb(MariadbTargetConfig),
    Sqlite(SqliteTargetConfig),
    Clickhouse(ClickhouseTargetConfig),
}

impl TargetConfig {
    /// Post-deserialize validation that crosses field boundaries. Delegates to
    /// the active variant's `validate()`.
    fn validate(&self) -> Result<()> {
        match self {
            TargetConfig::Postgres(t) => t.validate(),
            TargetConfig::Mariadb(t) => t.validate(),
            TargetConfig::Sqlite(t) => t.validate(),
            TargetConfig::Clickhouse(t) => t.validate(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct PostgresTargetConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    /// Legacy bool field from 0.3.x — accepted but never preferred. New configs
    /// should use `mode`. Top-level `target.overwrite: true` is still rejected
    /// at config-load by the legacy-form check (Task 13).
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    /// `overwrite` (default), `append`, or `create_if_missing`. All three are
    /// implemented in Rust as of MB-3 (sink full-mode parity).
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// When true, after upserting chunks for a document, delete any rows for
    /// that document with `seq_num >= len(new_chunks)`. Closes the per-doc
    /// shrink gap (last run wrote 12 chunks; this run writes 8 → drop the 4
    /// orphans inside the same write transaction). Default false to preserve
    /// the historical behavior. See `docs/incremental.md`.
    #[serde(default)]
    pub delete_orphans: bool,
}

impl PostgresTargetConfig {
    /// Post-deserialize validation that crosses field boundaries (e.g.
    /// mode/source_tag coupling). Identifier safety is enforced separately in
    /// `load_config` via `validate_ident`.
    fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct MariadbTargetConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    /// Legacy bool field from 0.3.x — accepted but never preferred. Same shape
    /// as PostgresTargetConfig.
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    #[serde(default)]
    pub delete_orphans: bool,
}

impl MariadbTargetConfig {
    pub(crate) fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClickhouseTargetConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// On ClickHouse, `delete_orphans: true` is a NO-OP that emits a single
    /// `tracing::warn!` per process. CH's `ALTER TABLE ... DELETE` is async
    /// and breaks chunkshop's per-document atomic write contract.
    #[serde(default)]
    pub delete_orphans: bool,
    /// Optional engine override. When `None`, the sink emits
    /// `MergeTree() ORDER BY (id)`. To opt into lazy dedup, set
    /// `"ReplacingMergeTree(created_at) ORDER BY (id)"`. Validated against
    /// `CLICKHOUSE_ENGINE_RE` at config-load — a Rust-only hardening relative
    /// to Python which interpolates the field raw.
    #[serde(default)]
    pub engine: Option<String>,
}

impl ClickhouseTargetConfig {
    fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        if let Some(e) = &self.engine {
            let re = Regex::new(CLICKHOUSE_ENGINE_RE).unwrap();
            if !re.is_match(e) {
                return Err(anyhow!(
                    "target.engine {e:?} not in allowlist. Accepted shapes: \
                     'MergeTree', 'MergeTree()', 'ReplacingMergeTree(<col>)', \
                     each optionally followed by ' ORDER BY <expr>'. Custom engines \
                     are not supported in v0.4 — file an issue if you need one."
                ));
            }
        }
        Ok(())
    }
}

/// SQLite target. Mirrors Python's `chunkshop.config.SqliteTarget`.
/// `database` is validated as a non-empty ident at config-load (loose parity
/// with Postgres) but ignored at runtime — SQLite has no schema namespace.
/// `target.hnsw=true` is a no-op on SQLite (sqlite-vec is brute-force KNN);
/// the sink emits a one-time process-level warning when set.
#[derive(Debug, Clone, Deserialize)]
pub struct SqliteTargetConfig {
    /// Env var holding the path to the SQLite file (or `:memory:`).
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    /// Legacy bool from 0.3.x — accepted but never preferred. New configs use `mode`.
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    /// `overwrite` (default), `append`, or `create_if_missing`.
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// Mirror PostgresTargetConfig.delete_orphans. Same per-doc-shrink semantics.
    #[serde(default)]
    pub delete_orphans: bool,
}

impl SqliteTargetConfig {
    pub(crate) fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        Ok(())
    }
}

fn default_dsn_env() -> String {
    "CHUNKSHOP_DSN".to_string()
}
fn default_hnsw() -> bool {
    true
}
fn default_mode() -> String {
    "overwrite".to_string()
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct RuntimeConfig {
    #[serde(default)]
    pub omp_num_threads: Option<usize>,
    #[serde(default)]
    pub doc_limit: Option<usize>,
    #[serde(default)]
    pub log_path: Option<String>,
    #[serde(default)]
    pub heartbeat_every: Option<usize>,
    /// "text" (default) or "json" — controls the CLI's tracing-subscriber
    /// formatter. JSON emits one structured event per line for log aggregators.
    #[serde(default = "default_log_format")]
    pub log_format: String,
}

fn default_log_format() -> String {
    "text".to_string()
}

/// Validate identifier against Python's regex: `^[a-z_][a-z0-9_]*$`.
fn validate_ident(name: &str, field: &str) -> Result<()> {
    let re = Regex::new(r"^[a-z_][a-z0-9_]*$").unwrap();
    if !re.is_match(name) {
        return Err(anyhow!(
            "{field} must match ^[a-z_][a-z0-9_]*$, got {name:?}"
        ));
    }
    Ok(())
}

/// Pre-deserialize legacy-form rejection (V4-SC-006).
///
/// Walks the raw YAML for known 0.3.x field/value patterns and emits a
/// migration-friendly error when found. Without this pass, serde's default
/// errors are cryptic ("unknown variant `pgvector`") or absent (silently
/// accepted legacy fields).
fn reject_legacy_forms(yaml: &serde_yaml_ng::Value) -> Result<()> {
    let target = yaml.get("target").and_then(|v| v.as_mapping());
    let Some(target) = target else {
        return Ok(()); // No target block; nothing to validate.
    };

    if let Some(t) = target.get("type").and_then(|v| v.as_str()) {
        if t == "pgvector" {
            return Err(anyhow!(
                "target.type 'pgvector' was renamed to 'postgres' in v0.4.0. Update your YAML."
            ));
        }
    }
    if target.get("schema").is_some() {
        return Err(anyhow!(
            "target.schema was renamed to target.database in v0.4.0. Update your YAML."
        ));
    }
    if let Some(o) = target.get("overwrite") {
        if matches!(o.as_bool(), Some(true)) {
            return Err(anyhow!(
                "target.overwrite: true was replaced by target.mode: 'overwrite' in v0.4.0. \
                 Update your YAML."
            ));
        }
    }
    Ok(())
}

pub fn load_config(path: &Path) -> Result<CellConfig> {
    let text = std::fs::read_to_string(path)
        .with_context(|| format!("reading config {}", path.display()))?;

    // V4-SC-006: reject 0.3.x legacy YAML shapes with friendly errors before
    // typed deserialization (which would emit cryptic "unknown variant" errors).
    let raw_value: serde_yaml_ng::Value = serde_yaml_ng::from_str(&text)
        .with_context(|| format!("parsing YAML at {}", path.display()))?;
    reject_legacy_forms(&raw_value)?;

    let cfg: CellConfig = serde_yaml_ng::from_str(&text)
        .with_context(|| format!("parsing YAML {}", path.display()))?;
    match &cfg.target {
        TargetConfig::Postgres(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
        TargetConfig::Mariadb(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
        TargetConfig::Sqlite(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
        TargetConfig::Clickhouse(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
    }
    if let SourceConfig::PgTable(p) = &cfg.source {
        validate_ident(&p.schema_name, "source.schema")?;
        validate_ident(&p.table, "source.table")?;
        validate_ident(&p.id_column, "source.id_column")?;
        validate_ident(&p.content_column, "source.content_column")?;
        if let Some(tc) = &p.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        // `where_clause` is intentionally NOT validated — see PgTableSourceConfig docstring.
    }
    if let SourceConfig::MariadbTable(p) = &cfg.source {
        validate_ident(&p.database_name, "source.database")?;
        validate_ident(&p.table, "source.table")?;
        validate_ident(&p.id_column, "source.id_column")?;
        validate_ident(&p.content_column, "source.content_column")?;
        if let Some(tc) = &p.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        // `where_clause` intentionally NOT validated — same contract as PgTableSourceConfig.
    }
    if let SourceConfig::SqliteTable(s) = &cfg.source {
        validate_ident(&s.database_name, "source.database")?;
        validate_ident(&s.table, "source.table")?;
        validate_ident(&s.id_column, "source.id_column")?;
        validate_ident(&s.content_column, "source.content_column")?;
        if let Some(tc) = &s.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        // `where_clause` intentionally NOT validated — same contract as PgTableSourceConfig.
    }
    if let SourceConfig::ClickhouseTable(p) = &cfg.source {
        validate_ident(&p.database_name, "source.database")?;
        validate_ident(&p.table, "source.table")?;
        validate_ident(&p.id_column, "source.id_column")?;
        validate_ident(&p.content_column, "source.content_column")?;
        if let Some(tc) = &p.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        for mc in &p.metadata_columns {
            validate_ident(mc, "source.metadata_columns")?;
        }
        // `where_clause` is intentionally NOT validated — see ClickhouseTableSourceConfig docstring.
    }
    cfg.target.validate()?;
    validate_chunker_config(&cfg.chunker)?;
    match &cfg.embedder {
        EmbedderConfig::Fastembed(e) => e.validate()?,
    }
    Ok(cfg)
}

/// Cross-field validation for chunker configs (recursive, walks any
/// `Box<ChunkerConfig>` base fields). Mirrors Python's pydantic model
/// validators that fire at config-load time.
fn validate_chunker_config(c: &ChunkerConfig) -> Result<()> {
    // Brief SC-001: `if_oversize` without an effective ceiling is nonsensical
    // — there's nothing to compare against. Reject at config-load.
    if c.if_oversize().is_some() && c.effective_max_chars().is_none() {
        return Err(anyhow!(
            "chunker {:?} has `if_oversize` set but no effective `max_chars` ceiling. \
             Either set `max_chars` on this chunker (or on its `base` for wrappers), \
             or remove `if_oversize`.",
            c.type_name()
        ));
    }
    // Recurse into the fallback chunker config so nested chains are validated.
    if let Some(nested) = c.if_oversize() {
        validate_chunker_config(nested)?;
    }
    match c {
        ChunkerConfig::SentenceAware(_)
        | ChunkerConfig::Hierarchy(_)
        | ChunkerConfig::FixedOverlap(_)
        | ChunkerConfig::Semantic(_) => Ok(()),
        ChunkerConfig::NeighborExpand(c) => validate_chunker_config(&c.base),
        ChunkerConfig::SummaryEmbed(c) => validate_chunker_config(&c.base),
        ChunkerConfig::HierarchicalSummary(c) => {
            // Mirror Python's _section_aware_requires_hierarchy_base: when
            // grouping is section_aware, the base chunker MUST be hierarchy.
            if matches!(c.grouping, GroupingConfig::SectionAware(_)) {
                let base_type_name = c.base.type_name();
                if base_type_name != "hierarchy" {
                    return Err(anyhow!(
                        "hierarchical_summary with strategy='section_aware' requires \
                         base.type='hierarchy', got {base_type_name:?}"
                    ));
                }
            }
            validate_chunker_config(&c.base)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_yaml(body: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!(
            "chunkshop-rs-cfg-{}.yaml",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::write(&path, body).unwrap();
        path
    }

    #[test]
    fn rejects_append_without_source_tag() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: append, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("source_tag"),
            "expected source_tag mention, got: {err}"
        );
    }

    #[test]
    fn rejects_invalid_promote_type() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: entities.ORG, type: bogus_type }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("type"),
            "expected promote_metadata type complaint, got: {err}"
        );
    }

    #[test]
    fn rejects_invalid_promote_path() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: "0entities.ORG", type: text }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("path"),
            "expected promote_metadata path complaint, got: {err}"
        );
    }

    #[test]
    fn promote_column_name_lowercases_and_double_underscores() {
        let pc: PromoteColumn =
            serde_yaml_ng::from_str("{ path: entities.ORG, type: \"text[]\" }").unwrap();
        assert_eq!(pc.column_name(), "entities__org");
    }

    #[test]
    fn parses_promote_metadata_into_typed_vec() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: heading, type: text }
    - { path: entities.ORG, type: "text[]" }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let TargetConfig::Postgres(t) = &cfg.target else {
            panic!("expected Postgres target");
        };
        assert_eq!(t.promote_metadata.len(), 2);
        assert_eq!(t.promote_metadata[0].path, "heading");
        assert_eq!(t.promote_metadata[0].type_, "text");
        assert_eq!(t.promote_metadata[1].column_name(), "entities__org");
    }

    #[test]
    fn rejects_section_aware_without_hierarchy_base() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: hierarchical_summary
  base: { type: sentence_aware }
  summarizer: { mode: passthrough }
  grouping: { strategy: section_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("section_aware") && err.contains("hierarchy"),
            "expected section_aware/hierarchy mention, got: {err}"
        );
    }

    #[test]
    fn parses_if_oversize_on_every_chunker_variant() {
        // Brief SC-001: every chunker variant accepts an optional if_oversize
        // pointing at any other chunker config.
        for kind in [
            "sentence_aware",
            "hierarchy",
            "fixed_overlap",
            "neighbor_expand",
            "semantic",
            "summary_embed",
            "hierarchical_summary",
        ] {
            let chunker_yaml = match kind {
                "sentence_aware" => "{ type: sentence_aware }".to_string(),
                "hierarchy" => "{ type: hierarchy }".to_string(),
                "fixed_overlap" => "{ type: fixed_overlap, max_chars: 1500 }".to_string(),
                "neighbor_expand" => {
                    "{ type: neighbor_expand, base: { type: hierarchy } }".to_string()
                }
                "semantic" => "{ type: semantic }".to_string(),
                "summary_embed" => "{ type: summary_embed, base: { type: hierarchy }, summarizer: { mode: passthrough } }".to_string(),
                "hierarchical_summary" => "{ type: hierarchical_summary, base: { type: hierarchy }, summarizer: { mode: passthrough } }".to_string(),
                _ => unreachable!(),
            };
            // Inline a minimal cell config and inject if_oversize on the chunker.
            let yaml = format!(
                r#"
cell_name: t
source: {{ type: files, glob: "x", id_from: stem }}
chunker:
  type: {kind}
  {extra}
  if_oversize:
    type: fixed_overlap
    window_words: 100
    step_words: 100
    max_chars: 500
embedder: {{ type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }}
target: {{ type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }}
"#,
                kind = kind,
                extra = match kind {
                    "fixed_overlap" => "max_chars: 1500".to_string(),
                    "neighbor_expand" => "base: { type: hierarchy }".to_string(),
                    "summary_embed" =>
                        "base: { type: hierarchy }\n  summarizer: { mode: passthrough }".to_string(),
                    "hierarchical_summary" =>
                        "base: { type: hierarchy }\n  summarizer: { mode: passthrough }"
                            .to_string(),
                    _ => "".to_string(),
                }
            );
            let _ = chunker_yaml; // suppress unused-var lint in this branch
            let path = write_yaml(&yaml);
            let cfg = load_config(&path).unwrap_or_else(|e| {
                panic!("if_oversize on {kind} failed to parse: {e:#}");
            });
            assert!(
                cfg.chunker.if_oversize().is_some(),
                "if_oversize missing for {kind}"
            );
        }
    }

    #[test]
    fn rejects_if_oversize_without_effective_ceiling() {
        // Brief SC-001 NEVER: fixed_overlap without max_chars and with
        // if_oversize is rejected at config-load — there's nothing to
        // compare against.
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: fixed_overlap
  window_words: 200
  step_words: 100
  if_oversize:
    type: fixed_overlap
    window_words: 100
    step_words: 50
    max_chars: 500
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("if_oversize") && err.contains("max_chars"),
            "expected if_oversize/max_chars complaint, got: {err}"
        );
    }

    #[test]
    fn effective_max_chars_falls_through_to_base() {
        // Brief SC-003: wrapper without explicit max_chars inherits from base.
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: neighbor_expand
  window: 2
  base:
    type: hierarchy
    max_chars: 1234
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        assert_eq!(cfg.chunker.effective_max_chars(), Some(1234));
    }

    #[test]
    fn fixed_overlap_max_chars_is_optional_unset() {
        // Brief SC-002: fixed_overlap without max_chars parses and resolves
        // to None (legacy word-only behavior).
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: fixed_overlap, window_words: 200, step_words: 100 }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        assert!(cfg.chunker.effective_max_chars().is_none());
    }

    #[test]
    fn accepts_section_aware_with_hierarchy_base() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: hierarchical_summary
  base: { type: hierarchy }
  summarizer: { mode: passthrough }
  grouping: { strategy: section_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        load_config(&path).expect("should accept section_aware over hierarchy base");
    }

    #[test]
    fn parses_sqlite_target_config() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        match &cfg.target {
            TargetConfig::Sqlite(t) => {
                assert_eq!(t.dsn_env, "SQLITE_PATH");
                assert_eq!(t.database_name, "ignored");
                assert_eq!(t.table, "chunks");
                assert_eq!(t.mode, "overwrite");
            }
            _ => panic!("expected Sqlite target"),
        }
    }

    #[test]
    fn rejects_sqlite_append_without_source_tag() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: append, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(err.contains("source_tag"), "expected source_tag mention, got: {err}");
    }

    #[test]
    fn parses_sqlite_table_source_config() {
        let yaml = r#"
cell_name: t
source:
  type: sqlite_table
  dsn_env: SQLITE_PATH
  database: ignored
  table: docs
  id_column: id
  content_column: body
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        match &cfg.source {
            SourceConfig::SqliteTable(s) => {
                assert_eq!(s.dsn_env, "SQLITE_PATH");
                assert_eq!(s.table, "docs");
                assert_eq!(s.id_column, "id");
            }
            _ => panic!("expected SqliteTable source"),
        }
    }

    #[test]
    fn parses_clickhouse_target() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: CHUNKSHOP_DSN_CH
  database: my_db
  table: chunks
  mode: overwrite
  hnsw: true
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let TargetConfig::Clickhouse(t) = &cfg.target else {
            panic!("expected Clickhouse variant");
        };
        assert_eq!(t.database_name, "my_db");
        assert_eq!(t.table, "chunks");
        assert!(t.engine.is_none());
    }

    #[test]
    fn accepts_replacing_merge_tree_engine() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "ReplacingMergeTree(created_at) ORDER BY (id)"
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("ReplacingMergeTree should be accepted");
        let TargetConfig::Clickhouse(t) = &cfg.target else { unreachable!() };
        assert_eq!(t.engine.as_deref(), Some("ReplacingMergeTree(created_at) ORDER BY (id)"));
    }

    #[test]
    fn rejects_arbitrary_engine_string() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "Memory"
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(err.contains("allowlist") && err.contains("Memory"), "got: {err}");
    }

    #[test]
    fn rejects_engine_with_drop_table_injection() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "MergeTree(); DROP TABLE other"
"#;
        let path = write_yaml(yaml);
        assert!(
            load_config(&path).is_err(),
            "engine with embedded DROP must be rejected"
        );
    }
}
