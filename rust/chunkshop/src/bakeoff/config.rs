//! Bakeoff config models. Mirrors `python/src/chunkshop/bakeoff/config.py`.
//!
//! `BakeoffConfig` round-trips YAML compatible with Python's bakeoff. The
//! `gold_queries` field is a string-or-list union (`#[serde(untagged)]`).

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::config::{ChunkerConfig, FastembedEmbedderConfig, FramerConfig, RuntimeConfig, SourceConfig};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct GoldQuery {
    pub query: String,
    pub gold_doc_id: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct MatrixConfig {
    pub embedders: Vec<FastembedEmbedderConfig>,
    pub chunkers: Vec<ChunkerConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BakeoffTargetConfig {
    pub dsn_env: String,
    #[serde(rename = "schema")]
    pub schema_name: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ScoringConfig {
    #[serde(default = "default_k")]
    pub k: Vec<usize>,
    #[serde(default = "default_include_mrr")]
    pub include_mrr: bool,
    #[serde(default = "default_top_k")]
    pub top_k: usize,
}

fn default_k() -> Vec<usize> {
    vec![1, 3, 5]
}
fn default_include_mrr() -> bool {
    true
}
fn default_top_k() -> usize {
    5
}

impl Default for ScoringConfig {
    fn default() -> Self {
        Self {
            k: default_k(),
            include_mrr: default_include_mrr(),
            top_k: default_top_k(),
        }
    }
}

/// `gold_queries` is either a path to a YAML/JSON file OR an inline list of
/// queries. `serde(untagged)` resolves at deserialize-time based on shape.
#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum GoldQueriesSpec {
    Inline(Vec<GoldQuery>),
    Path(String),
}

#[derive(Debug, Clone, Deserialize)]
pub struct BakeoffConfig {
    pub name: String,
    pub source: SourceConfig,
    #[serde(default)]
    pub framer: Option<FramerConfig>,
    pub gold_queries: GoldQueriesSpec,
    pub matrix: MatrixConfig,
    pub target: BakeoffTargetConfig,
    #[serde(default)]
    pub scoring: ScoringConfig,
    #[serde(default)]
    pub output_dir: Option<String>,
    #[serde(default)]
    pub runtime: Option<RuntimeConfig>,
}

/// One scored combo. The `aggregate` map mirrors Python's float-keyed dict
/// (`recall_at_1`, `recall_at_3`, …, `mrr`). BTreeMap so JSON output is
/// deterministic across runs (matches Python dict insertion order being
/// stable in 3.7+ for the score-key set we emit).
#[derive(Debug, Clone, Serialize)]
pub struct ComboResult {
    pub chunker_key: String,
    pub embedder_key: String,
    pub chunker_label: String,
    pub embedder_label: String,
    pub table: String,
    pub ingest_chunks: i64,
    pub ingest_wall_seconds: f64,
    /// Subset of `ingest_wall_seconds`: just the embedder. Distinguishes
    /// "slow because of the embedder" from "slow because of the chunker /
    /// sink". Mirrors Python's `ComboResult.ingest_embed_seconds`.
    #[serde(default)]
    pub ingest_embed_seconds: f64,
    pub aggregate: BTreeMap<String, f64>,
    pub per_query: Vec<PerQueryResult>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PerQueryResult {
    pub query: String,
    pub gold_doc_id: String,
    pub top_k: Vec<TopKHit>,
    /// Per-k recall: `recall_at_<k>: 0.0|1.0`. Plus `mrr`. Same key shape
    /// Python emits — keeps cross-language report.md byte-comparable.
    #[serde(flatten)]
    pub scores: BTreeMap<String, f64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TopKHit {
    pub doc_id: String,
    pub seq_num: i32,
}

#[derive(Debug, Clone, Serialize)]
pub struct BakeoffResults {
    pub run_name: String,
    pub started_at: String,
    pub corpus_label: String,
    pub n_queries: usize,
    pub n_combos: usize,
    pub combos: Vec<ComboResult>,
    pub gold_queries: Vec<GoldQuery>,
    /// Wall time per unique embedder spent embedding all gold queries
    /// during scoring. Predicts query-time latency at production scale.
    /// Mirrors Python's `query_embed_seconds_by_embedder`.
    #[serde(default)]
    pub query_embed_seconds_by_embedder: BTreeMap<String, f64>,
}
