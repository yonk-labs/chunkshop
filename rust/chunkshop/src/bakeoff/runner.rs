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
    BakeoffConfig, BakeoffResults, ComboResult, GoldQuery, PerQueryResult, TopKHit,
};
use super::gold::load_gold_queries_with_base;
use super::keys::{chunker_key, combo_table, embedder_key};
use super::score::{aggregate_scores, score_query};
use crate::config::{
    CellConfig, ChunkerConfig, EmbedderConfig, ExtractorConfig, FastembedEmbedderConfig,
    FramerConfig, IdentityFramerConfig, NoneExtractorConfig, SourceConfig, TargetConfig,
};
use crate::embedder::FastembedEmbedder;

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
        SourceConfig::Http(_) => "http".to_string(),
        SourceConfig::S3(s) => format!("s3://{}/{}", s.bucket, s.prefix),
        SourceConfig::Inline(_) => "inline".to_string(),
    }
}

fn build_cell_cfg(
    bakeoff: &BakeoffConfig,
    chunker_cfg: &ChunkerConfig,
    embedder_cfg: &FastembedEmbedderConfig,
    table: &str,
) -> Result<CellConfig> {
    let cell_name = format!(
        "{}__{}__{}",
        bakeoff.name,
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
        target: TargetConfig {
            dsn_env: bakeoff.target.dsn_env.clone(),
            schema_name: bakeoff.target.schema_name.clone(),
            table: table.to_string(),
            overwrite: false,
            hnsw: false,
            mode: "overwrite".to_string(),
            source_tag: None,
            promote_metadata: vec![],
            force_overwrite: false,
            delete_orphans: false,
        },
        runtime,
        framer,
        extractor: ExtractorConfig::None(NoneExtractorConfig {}),
    })
}

async fn count_chunks(dsn: &str, schema: &str, table: &str) -> Result<i64> {
    let pool = PgPoolOptions::new().max_connections(1).connect(dsn).await?;
    let stmt = format!(r#"SELECT COUNT(*) FROM "{schema}"."{table}""#);
    let row = sqlx::query(&stmt).fetch_one(&pool).await?;
    Ok(row.get::<i64, _>(0))
}

async fn query_top_k(
    dsn: &str,
    schema: &str,
    table: &str,
    query_vec: &[f32],
    k: usize,
) -> Result<Vec<TopKHit>> {
    let pool = PgPoolOptions::new().max_connections(1).connect(dsn).await?;
    let vec_str: String = format!(
        "[{}]",
        query_vec
            .iter()
            .map(|x| format!("{:.8}", x))
            .collect::<Vec<_>>()
            .join(",")
    );
    let stmt = format!(
        r#"SELECT doc_id, seq_num FROM "{schema}"."{table}" ORDER BY embedding <=> $1::vector LIMIT $2"#
    );
    let rows = sqlx::query(&stmt)
        .bind(&vec_str)
        .bind(k as i64)
        .fetch_all(&pool)
        .await?;
    Ok(rows
        .into_iter()
        .map(|r| TopKHit {
            doc_id: r.get::<String, _>(0),
            seq_num: r.get::<i32, _>(1),
        })
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
    let dsn = std::env::var(&cfg.target.dsn_env).map_err(|_| {
        anyhow!(
            "DSN env var {:?} is not set. The CLI sets it from --dsn before \
             calling run_bakeoff.",
            cfg.target.dsn_env
        )
    })?;
    let schema = &cfg.target.schema_name;

    let gold: Vec<GoldQuery> = load_gold_queries_with_base(&cfg.gold_queries, base_dir)?;

    // Build (chunker, embedder) cross-product. Order: outer = chunkers,
    // inner = embedders — same as Python's list comprehension.
    let mut combos_in: Vec<(ChunkerConfig, FastembedEmbedderConfig)> = Vec::new();
    for c in &cfg.matrix.chunkers {
        for e in &cfg.matrix.embedders {
            combos_in.push((c.clone(), e.clone()));
        }
    }

    // Format: "YYYY-MM-DD HH:MM:SS" UTC, matches Python's strftime in the
    // bakeoff runner so report.md timestamp prefix is comparable.
    let started_at = format_utc_now();

    // ----- Phase 1: ingest every combo serially -----
    struct IngestMeta {
        chunker: ChunkerConfig,
        embedder: FastembedEmbedderConfig,
        table: String,
        chunks: i64,
        wall_seconds: f64,
    }
    let mut ingest_meta: Vec<IngestMeta> = Vec::with_capacity(combos_in.len());

    for (c, e) in &combos_in {
        let table = combo_table(c, e)?;
        let cell_cfg = build_cell_cfg(cfg, c, e, &table)?;
        let t0 = Instant::now();
        let res = crate::runner::run_cell(cell_cfg)
            .await
            .with_context(|| format!("ingest failed for combo {table}"))?;
        let wall = t0.elapsed().as_secs_f64();
        let _ = res; // CellResult fields don't carry the table; we count below
        let chunks = count_chunks(&dsn, schema, &table).await?;
        ingest_meta.push(IngestMeta {
            chunker: c.clone(),
            embedder: e.clone(),
            table,
            chunks,
            wall_seconds: (wall * 100.0).round() / 100.0,
        });
    }

    // ----- Phase 2: embed gold queries once per unique embedder -----
    let mut query_vecs_by_emb_key: std::collections::HashMap<String, Vec<Vec<f32>>> =
        std::collections::HashMap::new();
    for e in &cfg.matrix.embedders {
        let k = embedder_key(e);
        if query_vecs_by_emb_key.contains_key(&k) {
            continue;
        }
        let mut embedder = FastembedEmbedder::new(e.clone())?;
        let texts: Vec<String> = gold.iter().map(|g| g.query.clone()).collect();
        let vecs = embedder.embed(texts)?;
        query_vecs_by_emb_key.insert(k, vecs);
    }

    // ----- Phase 3: score every combo -----
    let mut combo_results: Vec<ComboResult> = Vec::with_capacity(ingest_meta.len());
    for meta in &ingest_meta {
        let ck = chunker_key(&meta.chunker)?;
        let ek = embedder_key(&meta.embedder);
        let table = &meta.table;
        let vecs = query_vecs_by_emb_key
            .get(&ek)
            .ok_or_else(|| anyhow!("missing query vecs for embedder key {ek}"))?;

        let mut per_query: Vec<PerQueryResult> = Vec::with_capacity(gold.len());
        let mut per_query_scores: Vec<BTreeMap<String, f64>> = Vec::with_capacity(gold.len());
        for (i, g) in gold.iter().enumerate() {
            let top = query_top_k(&dsn, schema, table, &vecs[i], cfg.scoring.top_k).await?;
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
        combo_results.push(ComboResult {
            chunker_key: ck,
            embedder_key: ek,
            chunker_label: chunker_label(&meta.chunker),
            embedder_label: meta.embedder.model_name.clone(),
            table: table.clone(),
            ingest_chunks: meta.chunks,
            ingest_wall_seconds: meta.wall_seconds,
            aggregate: agg,
            per_query,
        });
    }

    Ok(BakeoffResults {
        run_name: cfg.name.clone(),
        started_at,
        corpus_label: corpus_label(cfg),
        n_queries: gold.len(),
        n_combos: combo_results.len(),
        combos: combo_results,
        gold_queries: gold,
    })
}
