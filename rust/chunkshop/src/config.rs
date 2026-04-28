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
    JsonCorpus(JsonCorpusSourceConfig),
}

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
}

impl TargetConfig {
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
    cfg.target.validate()?;
    Ok(cfg)
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
target: { dsn_env: D, schema: s, table: t, mode: append, hnsw: false }
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
  dsn_env: D
  schema: s
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
  dsn_env: D
  schema: s
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
            serde_yml::from_str("{ path: entities.ORG, type: \"text[]\" }").unwrap();
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
  dsn_env: D
  schema: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: heading, type: text }
    - { path: entities.ORG, type: "text[]" }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        assert_eq!(cfg.target.promote_metadata.len(), 2);
        assert_eq!(cfg.target.promote_metadata[0].path, "heading");
        assert_eq!(cfg.target.promote_metadata[0].type_, "text");
        assert_eq!(cfg.target.promote_metadata[1].column_name(), "entities__org");
    }
}
