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
    // Ignored stages (present in Python YAMLs, not implemented in Rust MVP):
    #[serde(default, skip_serializing)]
    #[allow(dead_code)]
    pub framer: Option<serde_yml::Value>,
    #[serde(default, skip_serializing)]
    #[allow(dead_code)]
    pub extractor: Option<serde_yml::Value>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SourceConfig {
    Files(FilesSourceConfig),
}

#[derive(Debug, Clone, Deserialize)]
pub struct FilesSourceConfig {
    pub glob: String,
    #[serde(default = "default_id_from")]
    pub id_from: String,
    #[serde(default = "default_encoding")]
    pub encoding: String,
}

fn default_id_from() -> String {
    "stem".to_string()
}

fn default_encoding() -> String {
    "utf-8".to_string()
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChunkerConfig {
    SentenceAware(SentenceAwareChunkerConfig),
    Hierarchy(HierarchyChunkerConfig),
}

#[derive(Debug, Clone, Deserialize)]
pub struct SentenceAwareChunkerConfig {
    #[serde(default = "default_doc_type")]
    pub doc_type: String,
    #[serde(default = "default_max_chars")]
    pub max_chars: usize,
    #[serde(default = "default_min_chars")]
    pub min_chars: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HierarchyChunkerConfig {
    #[serde(default = "default_prefix_heading")]
    pub prefix_heading: bool,
    #[serde(default = "default_min_section_chars")]
    pub min_section_chars: usize,
    #[serde(default = "default_max_chars")]
    pub max_chars: usize,
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
}

fn default_batch_size() -> usize {
    64
}

#[derive(Debug, Clone, Deserialize)]
pub struct TargetConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "schema")]
    pub schema_name: String,
    pub table: String,
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    /// `overwrite` (default), `append`, or `create_if_missing`. Rust MVP only
    /// implements `overwrite` and `create_if_missing`; `append` returns an
    /// error at runtime directing the user to the Python implementation.
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    /// Accepted in YAML but unused by the Rust MVP (no promoted-column writes).
    #[serde(default, skip_serializing)]
    #[allow(dead_code)]
    pub promote_metadata: Option<serde_yml::Value>,
    #[serde(default)]
    pub force_overwrite: bool,
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

pub fn load_config(path: &Path) -> Result<CellConfig> {
    let text = std::fs::read_to_string(path)
        .with_context(|| format!("reading config {}", path.display()))?;
    let cfg: CellConfig = serde_yml::from_str(&text)
        .with_context(|| format!("parsing YAML {}", path.display()))?;
    validate_ident(&cfg.target.schema_name, "target.schema")?;
    validate_ident(&cfg.target.table, "target.table")?;
    if let Some(tag) = &cfg.target.source_tag {
        validate_ident(tag, "target.source_tag")?;
    }
    Ok(cfg)
}
