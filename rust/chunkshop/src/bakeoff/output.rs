//! Output writers. Mirrors `python/src/chunkshop/bakeoff/output.py`.
//!
//! Three files land in `out_dir`:
//! - `results.json` — raw `BakeoffResults` as JSON
//! - `report.md`    — leaderboard + per-query detail + statistical-power note
//! - `recommended.yaml` — top-MRR combo as a runnable `CellConfig` YAML
//!
//! report.md formatting is deliberately byte-comparable with Python's so the
//! cross-language parity test can diff the leaderboard tables directly.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde_json::{json, Value};

use super::config::{BakeoffConfig, BakeoffResults, ComboResult};
use super::keys::{chunker_key, embedder_key};

pub fn write_results_json(results: &BakeoffResults, out_dir: &Path) -> Result<PathBuf> {
    let out = out_dir.join("results.json");
    let text = serde_json::to_string_pretty(results)?;
    std::fs::write(&out, text).with_context(|| format!("write {}", out.display()))?;
    Ok(out)
}

fn fmt_f3(x: f64) -> String {
    format!("{:.3}", x)
}

pub fn write_report_md(
    cfg: &BakeoffConfig,
    results: &BakeoffResults,
    out_dir: &Path,
) -> Result<PathBuf> {
    // Sort by MRR desc. Stable sort so Python's input order breaks ties.
    let mut ranked: Vec<&ComboResult> = results.combos.iter().collect();
    ranked.sort_by(|a, b| {
        let am = a.aggregate.get("mrr").copied().unwrap_or(0.0);
        let bm = b.aggregate.get("mrr").copied().unwrap_or(0.0);
        bm.partial_cmp(&am).unwrap_or(std::cmp::Ordering::Equal)
    });

    let header_cols = cfg
        .scoring
        .k
        .iter()
        .map(|k| format!("r@{k}"))
        .collect::<Vec<_>>()
        .join(" | ");
    // Columns: # | Chunker | Embedder | r@k... | MRR | chunks | ingest_s | embed_s
    let sep_cells = std::iter::repeat("---")
        .take(cfg.scoring.k.len() + 7)
        .collect::<Vec<_>>()
        .join("|");

    let mut lines: Vec<String> = vec![
        format!("# Bakeoff report: {}", results.run_name),
        String::new(),
        format!("- Run: {}", results.started_at),
        format!("- Corpus: {}", results.corpus_label),
        format!("- Queries: {}", results.n_queries),
        format!("- Combos: {}", results.n_combos),
        String::new(),
        "## Leaderboard (sorted by MRR)".into(),
        String::new(),
        format!("| # | Chunker | Embedder | {header_cols} | MRR | chunks | ingest_s | embed_s |"),
        format!("|{sep_cells}|"),
    ];
    for (i, c) in ranked.iter().enumerate() {
        let rk: Vec<String> = cfg
            .scoring
            .k
            .iter()
            .map(|k| {
                fmt_f3(
                    c.aggregate
                        .get(&format!("recall_at_{k}"))
                        .copied()
                        .unwrap_or(0.0),
                )
            })
            .collect();
        let mrr = fmt_f3(c.aggregate.get("mrr").copied().unwrap_or(0.0));
        lines.push(format!(
            "| {n} | `{chunker}` | `{embedder}` | {rks} | {mrr} | {chunks} | {ingest:.2} | {embed:.2} |",
            n = i + 1,
            chunker = c.chunker_label,
            embedder = c.embedder_label,
            rks = rk.join(" | "),
            mrr = mrr,
            chunks = c.ingest_chunks,
            ingest = c.ingest_wall_seconds,
            embed = c.ingest_embed_seconds,
        ));
    }

    lines.push(String::new());
    lines.push("## Per-query detail (top-1 hit per combo)".into());
    lines.push(String::new());
    lines.push("| Chunker | Embedder | Query | Gold | Top-1 | MRR |".into());
    lines.push("|---|---|---|---|---|---|".into());
    for c in &ranked {
        for pq in &c.per_query {
            let top1 = pq
                .top_k
                .first()
                .map(|h| h.doc_id.as_str())
                .unwrap_or("-");
            let mrr = fmt_f3(pq.scores.get("mrr").copied().unwrap_or(0.0));
            lines.push(format!(
                "| `{chunker}` | `{embedder}` | {query} | `{gold}` | `{top1}` | {mrr} |",
                chunker = c.chunker_label,
                embedder = c.embedder_label,
                query = pq.query,
                gold = pq.gold_doc_id,
            ));
        }
    }

    let n = results.n_queries.max(1) as f64;

    // Query-time embedding cost: per-embedder wall time during scoring.
    if !results.query_embed_seconds_by_embedder.is_empty() {
        lines.push(String::new());
        lines.push("## Query-time embedding cost".into());
        lines.push(String::new());
        lines.push(format!(
            "Wall time to embed all {} gold queries, per unique embedder. \
             At production scale this scales by your expected QPS — useful \
             for choosing between a slower-but-better embedder and a \
             faster-but-worse one.",
            results.n_queries
        ));
        lines.push(String::new());
        lines.push("| Embedder | total_s | per_query_ms |".into());
        lines.push("|---|---|---|".into());
        // Sort by total time ascending (fastest first).
        let mut entries: Vec<(&String, &f64)> = results
            .query_embed_seconds_by_embedder
            .iter()
            .collect();
        entries.sort_by(|a, b| a.1.partial_cmp(b.1).unwrap_or(std::cmp::Ordering::Equal));
        for (k, total) in entries {
            let per_q_ms = (total / n) * 1000.0;
            lines.push(format!("| `{k}` | {total:.3} | {per_q_ms:.1} |"));
        }
    }

    lines.push(String::new());
    lines.push("## Statistical power".into());
    lines.push(String::new());
    lines.push(format!(
        "{} queries means one query flipping moves aggregate recall by \
         {:.3}. Combos within ~{:.2} of the leader are not reliably \
         distinguishable. Re-run with more queries or a larger corpus before \
         treating the leaderboard as a tournament result.",
        results.n_queries,
        1.0 / n,
        2.0 / n
    ));
    lines.push(String::new());

    let out = out_dir.join("report.md");
    std::fs::write(&out, lines.join("\n")).with_context(|| format!("write {}", out.display()))?;
    Ok(out)
}

/// Render the top-MRR combo as a runnable CellConfig YAML.
///
/// Functional equivalence with Python's emission, not byte-equality:
/// - Same chunker config + same embedder config + same source/framer.
/// - cell_name suffix `_recommended` matches Python.
/// - Default mode = `overwrite`, schema = bakeoff schema, table = `{run_name}_production`.
pub fn write_recommended_yaml(
    cfg: &BakeoffConfig,
    results: &BakeoffResults,
    out_dir: &Path,
) -> Result<PathBuf> {
    let mut ranked: Vec<&ComboResult> = results.combos.iter().collect();
    ranked.sort_by(|a, b| {
        let am = a.aggregate.get("mrr").copied().unwrap_or(0.0);
        let bm = b.aggregate.get("mrr").copied().unwrap_or(0.0);
        bm.partial_cmp(&am).unwrap_or(std::cmp::Ordering::Equal)
    });
    let top = ranked
        .first()
        .ok_or_else(|| anyhow::anyhow!("no combos to recommend"))?;

    let winner_chunker = cfg
        .matrix
        .chunkers
        .iter()
        .find(|c| chunker_key(c).map(|k| k == top.chunker_key).unwrap_or(false))
        .ok_or_else(|| anyhow::anyhow!("winner chunker not found in matrix"))?;
    let winner_embedder = cfg
        .matrix
        .embedders
        .iter()
        .find(|e| embedder_key(e) == top.embedder_key)
        .ok_or_else(|| anyhow::anyhow!("winner embedder not found in matrix"))?;

    // Round-trip through serde_json::Value so we can drop fields the Python
    // version doesn't emit. Embedder/chunker/source/framer don't impl Serialize
    // (they're Deserialize-only), so we re-render them by re-serializing
    // through the matching shape. For simplicity and parity with Python, we
    // hand-build minimal maps from the discriminator + known fields.
    let chunker_yaml = chunker_to_yaml_value(winner_chunker)?;
    let embedder_yaml = embedder_to_yaml_value(winner_embedder);
    let source_yaml = source_to_yaml_value(&cfg.source);
    let framer_yaml = framer_to_yaml_value(
        cfg.framer
            .as_ref()
            .unwrap_or(&crate::config::FramerConfig::Identity(
                crate::config::IdentityFramerConfig {},
            )),
    );

    let note = format!(
        "Top combo from bakeoff '{}' (MRR={:.3}, r@1={:.3}). Point `source` \
         at your real corpus before running `chunkshop ingest`.",
        results.run_name,
        top.aggregate.get("mrr").copied().unwrap_or(0.0),
        top.aggregate.get("recall_at_1").copied().unwrap_or(0.0),
    );

    let recommended = json!({
        "# NOTE": note,
        "cell_name": format!("{}_recommended", results.run_name),
        "source": source_yaml,
        "framer": framer_yaml,
        "chunker": chunker_yaml,
        "embedder": embedder_yaml,
        "target": {
            "dsn_env": cfg.target.dsn_env,
            "schema": cfg.target.schema_name,
            "table": format!("{}_production", results.run_name),
            "mode": "overwrite",
        },
    });

    let yaml_text = serde_yml::to_string(&recommended)?;
    let out = out_dir.join("recommended.yaml");
    std::fs::write(&out, yaml_text).with_context(|| format!("write {}", out.display()))?;
    Ok(out)
}

fn chunker_to_yaml_value(c: &crate::config::ChunkerConfig) -> Result<Value> {
    use crate::config::ChunkerConfig as C;
    Ok(match c {
        C::Hierarchy(c) => json!({
            "type": "hierarchy",
            "prefix_heading": c.prefix_heading,
            "min_section_chars": c.min_section_chars,
            "max_chars": c.max_chars,
        }),
        C::SentenceAware(c) => json!({
            "type": "sentence_aware",
            "doc_type": c.doc_type,
            "max_chars": c.max_chars,
            "min_chars": c.min_chars,
        }),
        C::FixedOverlap(c) => json!({
            "type": "fixed_overlap",
            "window_words": c.window_words,
            "step_words": c.step_words,
        }),
        C::NeighborExpand(c) => json!({
            "type": "neighbor_expand",
            "base": chunker_to_yaml_value(&c.base)?,
            "window": c.window,
        }),
        C::Semantic(_) | C::SummaryEmbed(_) | C::HierarchicalSummary(_) => {
            return Err(anyhow::anyhow!(
                "recommended.yaml emission for this chunker variant is not implemented; \
                 these are out of the bakeoff matrix today."
            ));
        }
    })
}

fn embedder_to_yaml_value(e: &crate::config::FastembedEmbedderConfig) -> Value {
    let mut m = serde_json::Map::new();
    m.insert("type".into(), json!("fastembed"));
    m.insert("model_name".into(), json!(e.model_name));
    m.insert("dim".into(), json!(e.dim));
    m.insert("batch_size".into(), json!(e.batch_size));
    if let Some(t) = e.threads {
        m.insert("threads".into(), json!(t));
    }
    Value::Object(m)
}

fn source_to_yaml_value(s: &crate::config::SourceConfig) -> Value {
    use crate::config::SourceConfig as S;
    match s {
        S::Files(f) => json!({
            "type": "files",
            "glob": f.glob,
            "id_from": f.id_from,
            "encoding": f.encoding,
        }),
        S::JsonCorpus(j) => json!({
            "type": "json_corpus",
            "path": j.path,
            "documents_key": j.documents_key,
            "id_field": j.id_field,
            "content_field": j.content_field,
            "title_field": j.title_field,
        }),
        S::PgTable(p) => {
            let mut m = serde_json::Map::new();
            m.insert("type".into(), json!("pg_table"));
            m.insert("dsn_env".into(), json!(p.dsn_env));
            m.insert("schema".into(), json!(p.schema_name));
            m.insert("table".into(), json!(p.table));
            m.insert("id_column".into(), json!(p.id_column));
            m.insert("content_column".into(), json!(p.content_column));
            if let Some(t) = &p.title_column {
                m.insert("title_column".into(), json!(t));
            }
            if let Some(w) = &p.where_clause {
                m.insert("where".into(), json!(w));
            }
            Value::Object(m)
        }
        S::Http(h) => {
            let mut m = serde_json::Map::new();
            m.insert("type".into(), json!("http"));
            m.insert("urls".into(), json!(h.urls));
            if let Some(s) = &h.sitemap {
                m.insert("sitemap".into(), json!(s));
            }
            Value::Object(m)
        }
        S::S3(s3) => {
            let mut m = serde_json::Map::new();
            m.insert("type".into(), json!("s3"));
            m.insert("bucket".into(), json!(s3.bucket));
            m.insert("prefix".into(), json!(s3.prefix));
            if let Some(e) = &s3.endpoint_url {
                m.insert("endpoint_url".into(), json!(e));
            }
            Value::Object(m)
        }
        S::ClickhouseTable(c) => {
            let mut m = serde_json::Map::new();
            m.insert("type".into(), json!("clickhouse_table"));
            m.insert("dsn_env".into(), json!(c.dsn_env));
            m.insert("database".into(), json!(c.database_name));
            m.insert("table".into(), json!(c.table));
            m.insert("id_column".into(), json!(c.id_column));
            m.insert("content_column".into(), json!(c.content_column));
            if let Some(t) = &c.title_column {
                m.insert("title_column".into(), json!(t));
            }
            if let Some(w) = &c.where_clause {
                m.insert("where".into(), json!(w));
            }
            if !c.metadata_columns.is_empty() {
                m.insert("metadata_columns".into(), json!(c.metadata_columns));
            }
            Value::Object(m)
        }
        S::Inline(_) => json!({ "type": "inline" }),
    }
}

fn framer_to_yaml_value(f: &crate::config::FramerConfig) -> Value {
    use crate::config::FramerConfig as F;
    match f {
        F::Identity(_) => json!({ "type": "identity" }),
        F::HeadingBoundary(h) => {
            let mut m = serde_json::Map::new();
            m.insert("type".into(), json!("heading_boundary"));
            m.insert("pattern".into(), json!(h.pattern));
            m.insert("title_from_heading".into(), json!(h.title_from_heading));
            Value::Object(m)
        }
        F::RegexBoundary(r) => {
            let mut m = serde_json::Map::new();
            m.insert("type".into(), json!("regex_boundary"));
            m.insert("split_pattern".into(), json!(r.split_pattern));
            if let Some(p) = &r.title_pattern {
                m.insert("title_pattern".into(), json!(p));
            }
            m.insert("body_starts_with_match".into(), json!(r.body_starts_with_match));
            Value::Object(m)
        }
        F::Jsonpath(j) => {
            let mut m = serde_json::Map::new();
            m.insert("type".into(), json!("jsonpath"));
            m.insert("row_path".into(), json!(j.row_path));
            m.insert("body_path".into(), json!(j.body_path));
            if let Some(t) = &j.title_path {
                m.insert("title_path".into(), json!(t));
            }
            Value::Object(m)
        }
    }
}
