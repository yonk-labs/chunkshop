//! `run_bakeoff(cfg) -> BakeoffResults`. Async port of
//! `python/src/chunkshop/bakeoff/runner.py`.
//!
//! Phase 1: serial cross-product over `(chunkers x embedders)`. For each
//! combo, synthesize a `CellConfig` and call `run_cell` from the existing
//! single-cell runner — reuses the full pipeline.
//!
//! Phase 2: embed all gold queries once per UNIQUE embedder (combos sharing
//! an embedder share the same query vectors).
//!
//! Phase 3: per-combo pgvector top-K + per-query scoring + aggregation.
//!
//! No subprocess isolation here — that's the orchestrator's job. Matrix
//! size > 50 is the caller's call; CLI prompts, runner runs whatever it's
//! given.

use std::collections::BTreeMap;
use std::time::Instant;

use anyhow::{anyhow, Context, Result};
use sqlx::postgres::PgPoolOptions;
use sqlx::Row;

use super::config::{
    BakeoffConfig, BakeoffResults, BakeoffTargetEntry, ComboResult, GoldQuery, PerQueryResult, TopKHit,
};
use super::gold::load_gold_queries_with_base;
use super::keys::{chunker_key, combo_table, embedder_key};
use super::score::{aggregate_scores, score_query};
use crate::config::{
    CellConfig, ChunkerConfig, ClickhouseTargetConfig, EmbedderConfig, ExtractorConfig,
    FastembedEmbedderConfig, FramerConfig, IdentityFramerConfig, MariadbTargetConfig,
    NoneExtractorConfig, PostgresTargetConfig, SourceConfig, SqliteTargetConfig, TargetConfig,
};
use crate::embedder::FastembedEmbedder;
use crate::sinks::Sink;

/// Human-readable chunker label for the report.md leaderboard. Matches
/// Python's `_chunker_label` shape exactly so the rendered tables diff
/// cleanly across languages.
pub fn chunker_label(cfg: &ChunkerConfig) -> String {
    match cfg {
        ChunkerConfig::Hierarchy(_) => "hierarchy".to_string(),
        ChunkerConfig::SentenceAware(_) => "sentence_aware".to_string(),
        ChunkerConfig::FixedOverlap(c) => {
            format!(
                "fixed_overlap(window_words={}, step_words={})",
                c.window_words, c.step_words
            )
        }
        ChunkerConfig::NeighborExpand(c) => {
            format!(
                "neighbor_expand(window={}, base={})",
                c.window,
                chunker_label(&c.base)
            )
        }
        ChunkerConfig::Semantic(_) => "semantic".to_string(),
        ChunkerConfig::SummaryEmbed(_) => "summary_embed".to_string(),
        ChunkerConfig::HierarchicalSummary(_) => "hierarchical_summary".to_string(),
        ChunkerConfig::Consolidation(_) => "consolidation".to_string(),
    }
}

/// `YYYY-MM-DD HH:MM:SS` UTC string. Matches Python's
/// `time.strftime("%Y-%m-%d %H:%M:%S")` shape (Python's version uses local
/// time; ours is UTC — close enough for a report header timestamp, and
/// avoids tz-DB deps).
fn format_utc_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Days since 1970-01-01 + seconds within day → calendar date.
    // Algorithm: civil_from_days, en.wikipedia.org/wiki/Julian_day#Julian_or_Gregorian_calendar_from_Julian_day_number
    let days = (secs / 86_400) as i64;
    let sec_of_day = secs % 86_400;
    let hour = sec_of_day / 3600;
    let minute = (sec_of_day % 3600) / 60;
    let second = sec_of_day % 60;

    let z = days + 719_468;
    let era = if z >= 0 { z / 146_097 } else { (z - 146_096) / 146_097 };
    let doe = (z - era * 146_097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if m <= 2 { y + 1 } else { y };
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        year, m, d, hour, minute, second
    )
}

fn corpus_label(cfg: &BakeoffConfig) -> String {
    match &cfg.source {
        SourceConfig::Files(f) => f.glob.clone(),
        SourceConfig::JsonCorpus(j) => j.path.clone(),
        SourceConfig::PgTable(p) => format!("pg:{}.{}", p.schema_name, p.table),
        SourceConfig::MariadbTable(p) => format!("mariadb:{}.{}", p.database_name, p.table),
        SourceConfig::SqliteTable(s) => format!("sqlite:{}", s.table),
        SourceConfig::Http(_) => "http".to_string(),
        SourceConfig::S3(s) => format!("s3://{}/{}", s.bucket, s.prefix),
        SourceConfig::ClickhouseTable(c) => format!("ch:{}.{}", c.database_name, c.table),
        SourceConfig::SessionStaging(s) => {
            format!("memory:{}.{}", s.staging_schema, s.staging_table)
        }
        SourceConfig::Inline(_) => "inline".to_string(),
    }
}

/// Materialize a per-cell TargetConfig from a bakeoff target entry. Each
/// backend writes to a per-combo table under the target's database/schema.
/// `hnsw: false` for bakeoff cells across all backends — fair query-time
/// comparison without ANN approximation skew.
fn build_target_for_combo(target: &BakeoffTargetEntry, table: &str) -> TargetConfig {
    match target {
        BakeoffTargetEntry::Postgres(t) => TargetConfig::Postgres(PostgresTargetConfig {
            dsn_env: t.dsn_env.clone(),
            database_name: t.database_name.clone(),
            table: table.to_string(),
            overwrite: false,
            hnsw: false,
            mode: "overwrite".to_string(),
            source_tag: None,
            promote_metadata: vec![],
            force_overwrite: false,
            delete_orphans: false,
            memory: None,
        }),
        BakeoffTargetEntry::Mariadb(t) => TargetConfig::Mariadb(MariadbTargetConfig {
            dsn_env: t.dsn_env.clone(),
            database_name: t.database_name.clone(),
            table: table.to_string(),
            overwrite: false,
            hnsw: false,
            mode: "overwrite".to_string(),
            source_tag: None,
            promote_metadata: vec![],
            force_overwrite: false,
            delete_orphans: false,
        }),
        BakeoffTargetEntry::Sqlite(t) => TargetConfig::Sqlite(SqliteTargetConfig {
            dsn_env: t.dsn_env.clone(),
            database_name: t.database_name.clone(),
            table: table.to_string(),
            overwrite: false,
            hnsw: false,
            mode: "overwrite".to_string(),
            source_tag: None,
            promote_metadata: vec![],
            force_overwrite: false,
            delete_orphans: false,
        }),
        BakeoffTargetEntry::Clickhouse(t) => TargetConfig::Clickhouse(ClickhouseTargetConfig {
            dsn_env: t.dsn_env.clone(),
            database_name: t.database_name.clone(),
            table: table.to_string(),
            hnsw: false,
            mode: "overwrite".to_string(),
            source_tag: None,
            promote_metadata: vec![],
            force_overwrite: false,
            delete_orphans: false,
            engine: t.engine.clone(),
        }),
    }
}

fn build_cell_cfg(
    bakeoff: &BakeoffConfig,
    target: &BakeoffTargetEntry,
    chunker_cfg: &ChunkerConfig,
    embedder_cfg: &FastembedEmbedderConfig,
    table: &str,
) -> Result<CellConfig> {
    let cell_name = format!(
        "{}__{}__{}__{}",
        bakeoff.name,
        target.backend_name(),
        chunker_key(chunker_cfg)?,
        embedder_key(embedder_cfg)
    );
    let runtime = bakeoff.runtime.clone().unwrap_or_default();
    let framer = bakeoff
        .framer
        .clone()
        .unwrap_or_else(|| FramerConfig::Identity(IdentityFramerConfig::default()));
    Ok(CellConfig {
        cell_name,
        source: bakeoff.source.clone(),
        chunker: chunker_cfg.clone(),
        embedder: EmbedderConfig::Fastembed(embedder_cfg.clone()),
        target: build_target_for_combo(target, table),
        runtime,
        framer,
        extractor: ExtractorConfig::None(NoneExtractorConfig {}),
    })
}

/// Backend-dispatched chunk count. Each backend has a slightly different
/// fully-qualified table name shape so we can't share one SQL string.
async fn count_chunks(target: &BakeoffTargetEntry, table: &str) -> Result<i64> {
    match target {
        BakeoffTargetEntry::Postgres(t) => {
            let dsn = std::env::var(&t.dsn_env)?;
            let pool = PgPoolOptions::new().max_connections(1).connect(&dsn).await?;
            let stmt = format!(r#"SELECT COUNT(*) FROM "{}"."{}""#, t.database_name, table);
            let row = sqlx::query(&stmt).fetch_one(&pool).await?;
            Ok(row.get::<i64, _>(0))
        }
        BakeoffTargetEntry::Mariadb(t) => {
            use sqlx::mysql::MySqlPoolOptions;
            let dsn = std::env::var(&t.dsn_env)?;
            let pool = MySqlPoolOptions::new().max_connections(1).connect(&dsn).await?;
            let stmt = format!("SELECT COUNT(*) FROM `{}`.`{}`", t.database_name, table);
            let row = sqlx::query(&stmt).fetch_one(&pool).await?;
            Ok(row.get::<i64, _>(0))
        }
        BakeoffTargetEntry::Sqlite(t) => {
            // SQLite path is in the env var; use rusqlite directly.
            let path = std::env::var(&t.dsn_env)?;
            let conn = rusqlite::Connection::open(&path)?;
            let n: i64 = conn.query_row(
                &format!(r#"SELECT COUNT(*) FROM "{}""#, table),
                [],
                |r| r.get(0),
            )?;
            Ok(n)
        }
        BakeoffTargetEntry::Clickhouse(t) => {
            use crate::backends::ClickhouseBackend;
            let backend = ClickhouseBackend::new(t.dsn_env.clone());
            let client = backend.client().await?;
            #[derive(clickhouse::Row, serde::Deserialize)]
            struct CountRow {
                c: u64,
            }
            let mut cur = client
                .query(&format!(
                    "SELECT count() AS c FROM `{}`.`{}`",
                    t.database_name, table
                ))
                .fetch::<CountRow>()?;
            let row = cur
                .next()
                .await?
                .ok_or_else(|| anyhow!("count() returned no rows"))?;
            Ok(row.c as i64)
        }
    }
}

/// Backend-dispatched top-K via the Sink trait. Bakeoff cells are built with
/// `hnsw: false` so the comparison across backends is fair (all approximate-
/// vs-exact tradeoffs disabled). The sink's own query_top_k handles dialect
/// differences (cosine vs hybrid-euclidean on MariaDB, JOIN on vec0 for SQLite,
/// cosineDistance on CH, etc.).
async fn query_top_k_via_sink(
    target: &BakeoffTargetEntry,
    table: &str,
    query_vec: &[f32],
    k: usize,
    embed_dim: usize,
) -> Result<Vec<TopKHit>> {
    use crate::backends::load_backend;
    let target_cfg = build_target_for_combo(target, table);
    let backend = load_backend(&target_cfg)?;
    let sink = crate::sinks::load_sink(&target_cfg, backend, embed_dim)?;
    let results = sink.query_top_k(query_vec, k).await?;
    Ok(results
        .into_iter()
        .map(|(doc_id, seq_num, _dist)| TopKHit { doc_id, seq_num })
        .collect())
}

/// Execute every combo, score against gold, return BakeoffResults.
///
/// Caller must set `std::env::set_var(cfg.target.dsn_env, dsn)` before
/// calling — the sink reads the DSN from env. Errors out if unset.
pub async fn run_bakeoff(cfg: &BakeoffConfig) -> Result<BakeoffResults> {
    run_bakeoff_with_base(cfg, None).await
}

/// Like `run_bakeoff`, but resolves relative `gold_queries` paths against
/// `base_dir` when provided. CLI passes the bakeoff YAML's parent so paths
/// in the YAML behave like paths-relative-to-the-YAML.
pub async fn run_bakeoff_with_base(
    cfg: &BakeoffConfig,
    base_dir: Option<&std::path::Path>,
) -> Result<BakeoffResults> {
    let targets = cfg.effective_targets()?;
    // Verify every target's DSN env var is set before doing any work.
    for t in &targets {
        let var = t.dsn_env();
        std::env::var(var).map_err(|_| {
            anyhow!("DSN env var {var:?} is not set (required for {} target)", t.backend_name())
        })?;
    }

    let gold: Vec<GoldQuery> = load_gold_queries_with_base(&cfg.gold_queries, base_dir)?;

    // Build (chunker, embedder) cross-product per target. Order: outer = chunkers,
    // inner = embedders — same as Python.
    let mut chunker_embedder_combos: Vec<(ChunkerConfig, FastembedEmbedderConfig)> = Vec::new();
    for c in &cfg.matrix.chunkers {
        for e in &cfg.matrix.embedders {
            chunker_embedder_combos.push((c.clone(), e.clone()));
        }
    }

    let started_at = format_utc_now();

    // ----- Phase 2: embed gold queries once per unique embedder (shared across targets) -----
    let mut query_vecs_by_emb_key: std::collections::HashMap<String, Vec<Vec<f32>>> =
        std::collections::HashMap::new();
    let mut query_embed_seconds_by_emb_key: BTreeMap<String, f64> = BTreeMap::new();
    for e in &cfg.matrix.embedders {
        let k = embedder_key(e);
        if query_vecs_by_emb_key.contains_key(&k) {
            continue;
        }
        let mut embedder = FastembedEmbedder::new(e.clone())?;
        let texts: Vec<String> = gold.iter().map(|g| g.query.clone()).collect();
        let t_qe = Instant::now();
        let vecs = embedder.embed(texts)?;
        let qe_seconds = (t_qe.elapsed().as_secs_f64() * 1000.0).round() / 1000.0;
        query_embed_seconds_by_emb_key.insert(k.clone(), qe_seconds);
        query_vecs_by_emb_key.insert(k, vecs);
    }

    // ----- Phase 1 + 3: per-target, ingest + score every combo -----
    let mut combo_results: Vec<ComboResult> = Vec::new();

    for target in &targets {
        let backend_name = target.backend_name().to_string();

        for (c, e) in &chunker_embedder_combos {
            let table = combo_table(c, e)?;
            let cell_cfg = build_cell_cfg(cfg, target, c, e, &table)?;
            let t0 = Instant::now();
            let res = crate::runner::run_cell(cell_cfg).await.with_context(|| {
                format!(
                    "ingest failed for combo {table} on backend {backend_name}"
                )
            })?;
            let wall = t0.elapsed().as_secs_f64();
            let chunks = count_chunks(target, &table).await?;

            let ck = chunker_key(c)?;
            let ek = embedder_key(e);
            let vecs = query_vecs_by_emb_key
                .get(&ek)
                .ok_or_else(|| anyhow!("missing query vecs for embedder key {ek}"))?;

            let mut per_query: Vec<PerQueryResult> = Vec::with_capacity(gold.len());
            let mut per_query_scores: Vec<BTreeMap<String, f64>> = Vec::with_capacity(gold.len());
            let mut query_walls_ms: Vec<f64> = Vec::with_capacity(gold.len());
            for (i, g) in gold.iter().enumerate() {
                let tq = Instant::now();
                let top =
                    query_top_k_via_sink(target, &table, &vecs[i], cfg.scoring.top_k, e.dim)
                        .await?;
                query_walls_ms.push(tq.elapsed().as_secs_f64() * 1000.0);
                let doc_ids: Vec<String> = top.iter().map(|h| h.doc_id.clone()).collect();
                let s = score_query(&doc_ids, &g.gold_doc_id, &cfg.scoring.k);
                per_query_scores.push(s.clone());
                per_query.push(PerQueryResult {
                    query: g.query.clone(),
                    gold_doc_id: g.gold_doc_id.clone(),
                    top_k: top,
                    scores: s,
                });
            }

            let agg = aggregate_scores(&per_query_scores);
            let query_wall_ms_mean = if query_walls_ms.is_empty() {
                0.0
            } else {
                query_walls_ms.iter().sum::<f64>() / query_walls_ms.len() as f64
            };

            combo_results.push(ComboResult {
                backend: backend_name.clone(),
                chunker_key: ck,
                embedder_key: ek,
                chunker_label: chunker_label(c),
                embedder_label: e.model_name.clone(),
                table,
                ingest_chunks: chunks,
                ingest_wall_seconds: (wall * 100.0).round() / 100.0,
                ingest_embed_seconds: (res.embed_seconds * 100.0).round() / 100.0,
                query_wall_ms_mean: (query_wall_ms_mean * 100.0).round() / 100.0,
                aggregate: agg,
                per_query,
            });
        }
    }

    Ok(BakeoffResults {
        run_name: cfg.name.clone(),
        started_at,
        corpus_label: corpus_label(cfg),
        n_queries: gold.len(),
        n_combos: combo_results.len(),
        combos: combo_results,
        gold_queries: gold,
        query_embed_seconds_by_embedder: query_embed_seconds_by_emb_key,
    })
}
