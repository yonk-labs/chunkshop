//! Bakeoff config models. Mirrors `python/src/chunkshop/bakeoff/config.py`.
//!
//! `BakeoffConfig` round-trips YAML compatible with Python's bakeoff. The
//! `gold_queries` field is a string-or-list union (`#[serde(untagged)]`).

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::config::{
    ChunkerConfig, FastembedEmbedderConfig, FramerConfig, RuntimeConfig, SourceConfig,
};

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

/// Legacy single-PG bakeoff target. Kept for backward compatibility with
/// pre-v0.4.1 bakeoff YAMLs that use `target:` (singular) with a `schema:`
/// field. New configs should use `targets:` (plural list of BakeoffTargetEntry).
#[derive(Debug, Clone, Deserialize)]
pub struct BakeoffTargetConfig {
    pub dsn_env: String,
    #[serde(rename = "schema")]
    pub schema_name: String,
}

/// One backend target in a multi-target bakeoff. Mirrors Python's
/// `BakeoffTarget` discriminated union shape. Each variant carries the
/// minimal fields needed to materialize a chunkshop `TargetConfig`.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum BakeoffTargetEntry {
    Postgres(BakeoffPostgresTarget),
    Mariadb(BakeoffMariadbTarget),
    Sqlite(BakeoffSqliteTarget),
    Clickhouse(BakeoffClickhouseTarget),
}

#[derive(Debug, Clone, Deserialize)]
pub struct BakeoffPostgresTarget {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    #[serde(default = "default_vector_metric")]
    pub vector_metric: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BakeoffMariadbTarget {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BakeoffSqliteTarget {
    pub dsn_env: String,
    #[serde(rename = "database", default = "default_sqlite_db_name")]
    pub database_name: String,
}

fn default_sqlite_db_name() -> String {
    "ignored".to_string()
}

fn default_vector_metric() -> String {
    "cosine".to_string()
}

#[derive(Debug, Clone, Deserialize)]
pub struct BakeoffClickhouseTarget {
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    #[serde(default)]
    pub engine: Option<String>,
}

impl BakeoffTargetEntry {
    pub fn dsn_env(&self) -> &str {
        match self {
            Self::Postgres(t) => &t.dsn_env,
            Self::Mariadb(t) => &t.dsn_env,
            Self::Sqlite(t) => &t.dsn_env,
            Self::Clickhouse(t) => &t.dsn_env,
        }
    }
    pub fn database_name(&self) -> &str {
        match self {
            Self::Postgres(t) => &t.database_name,
            Self::Mariadb(t) => &t.database_name,
            Self::Sqlite(t) => &t.database_name,
            Self::Clickhouse(t) => &t.database_name,
        }
    }
    pub fn backend_name(&self) -> &'static str {
        match self {
            Self::Postgres(_) => "postgres",
            Self::Mariadb(_) => "mariadb",
            Self::Sqlite(_) => "sqlite",
            Self::Clickhouse(_) => "clickhouse",
        }
    }
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
    /// Legacy single-target form (`target:` with `schema:`). When present
    /// and `targets:` is empty, treated as a one-entry Postgres target list.
    /// Mutually exclusive with `targets:`; configs should set one or the
    /// other.
    #[serde(default)]
    pub target: Option<BakeoffTargetConfig>,
    /// Multi-backend target list. Each entry produces one full pass of the
    /// chunker × embedder matrix. Same shape as Python's `targets:` field.
    #[serde(default)]
    pub targets: Vec<BakeoffTargetEntry>,
    #[serde(default)]
    pub scoring: ScoringConfig,
    #[serde(default)]
    pub output_dir: Option<String>,
    #[serde(default)]
    pub runtime: Option<RuntimeConfig>,
}

impl BakeoffConfig {
    /// Resolve `target` + `targets` into a single non-empty list. Legacy
    /// `target:` configs get wrapped as a one-entry Postgres list; modern
    /// `targets:` configs pass through. Errors if BOTH are set (ambiguous)
    /// or if NEITHER is set.
    pub fn effective_targets(&self) -> anyhow::Result<Vec<BakeoffTargetEntry>> {
        match (&self.target, self.targets.is_empty()) {
            (Some(_), false) => Err(anyhow::anyhow!(
                "bakeoff YAML has BOTH 'target:' (legacy single-PG) and 'targets:' \
                 (multi-backend) — set exactly one."
            )),
            (Some(legacy), true) => Ok(vec![BakeoffTargetEntry::Postgres(BakeoffPostgresTarget {
                dsn_env: legacy.dsn_env.clone(),
                database_name: legacy.schema_name.clone(),
                vector_metric: default_vector_metric(),
            })]),
            (None, false) => Ok(self.targets.clone()),
            (None, true) => Err(anyhow::anyhow!(
                "bakeoff YAML must set either 'target:' (legacy single-PG) or \
                 'targets:' (multi-backend)."
            )),
        }
    }
}

/// One scored combo. The `aggregate` map mirrors Python's float-keyed dict
/// (`recall_at_1`, `recall_at_3`, …, `mrr`). BTreeMap so JSON output is
/// deterministic across runs (matches Python dict insertion order being
/// stable in 3.7+ for the score-key set we emit).
#[derive(Debug, Clone, Serialize)]
pub struct ComboResult {
    /// Backend name (`"postgres"` / `"mariadb"` / `"sqlite"` / `"clickhouse"`).
    /// Always set in v0.4.1+; legacy single-PG bakeoffs report `"postgres"`.
    #[serde(default)]
    pub backend: String,
    pub chunker_key: String,
    pub embedder_key: String,
    pub chunker_label: String,
    pub embedder_label: String,
    pub table: String,
    pub ingest_chunks: i64,
    pub ingest_wall_seconds: f64,
    /// Mean wall time per query against this combo (ms). Mirrors Python's
    /// `query_wall_seconds` (which is per-cell-summed; this is per-query
    /// mean to make cross-backend comparison easy).
    #[serde(default)]
    pub query_wall_ms_mean: f64,
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
