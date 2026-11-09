//! #75: PgSink::query_top_k_filtered splices WHERE predicates before ORDER BY
//! in a single query. Covers jsonb-containment (`metadata @> $::jsonb`) and
//! promoted-column equality. Skips when CHUNKSHOP_TEST_DSN is unset.

use chunkshop::backends::PostgresBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::{PostgresTargetConfig, PromoteColumn};
use chunkshop::sinks::{Filters, PgSink, Sink};
use serde_json::json;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

fn cfg(db: &str, promote: Vec<PromoteColumn>) -> PostgresTargetConfig {
    PostgresTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: db.to_string(),
        table: "chunks".to_string(),
        overwrite: false,
        hnsw: false,
        vector_metric: "cosine".to_string(),
        mode: "overwrite".to_string(),
        source_tag: Some("t1".to_string()),
        promote_metadata: promote,
        force_overwrite: false,
        delete_orphans: false,
        memory: None,
        documents: None,
    }
}

fn mk(doc: &str, seq: usize, tenant: &str) -> Chunk {
    Chunk {
        doc_id: doc.into(),
        seq_num: seq,
        original_content: format!("{doc}{seq}"),
        embedded_content: format!("{doc}{seq}"),
        metadata: json!({ "tenant": tenant }),
    }
}

// Two tenants nearest-mixed: a "globex" chunk is nearer to the query than two
// "acme" chunks, so the filter (not just ranking) is what excludes it.
fn scoped_corpus() -> (Vec<Chunk>, Vec<Vec<f32>>, Vec<Chunk>, Vec<Vec<f32>>) {
    let a: Vec<Chunk> = (0..3).map(|i| mk("a", i, "acme")).collect();
    let b: Vec<Chunk> = (0..3).map(|i| mk("b", i, "globex")).collect();
    let a_embs = vec![
        vec![1.0, 0.0, 0.0, 0.0],
        vec![0.0, 0.0, 1.0, 0.0],
        vec![0.0, 0.0, 0.0, 1.0],
    ];
    let b_embs = vec![
        vec![0.95, 0.05, 0.0, 0.0],
        vec![0.0, 1.0, 0.0, 0.0],
        vec![0.0, 0.0, 1.0, 0.0],
    ];
    (a, a_embs, b, b_embs)
}

#[tokio::test]
async fn metadata_containment_filter_returns_only_matching_scope() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_meta";
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = PgSink::new(cfg(db, vec![]), backend, 4);
    sink.create_table().await?;

    let (a, a_embs, b, b_embs) = scoped_corpus();
    sink.write_document("a", &a, &a_embs, &vec![vec![]; 3]).await?;
    sink.write_document("b", &b, &b_embs, &vec![vec![]; 3]).await?;

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let mut f = Filters::default();
    f.metadata.insert("tenant".into(), json!("acme"));
    let results = sink.query_top_k_filtered(&q, 10, Some(&f)).await?;

    assert_eq!(results.len(), 3, "all acme rows, no globex rows");
    assert!(results.iter().all(|r| r.0 == "a"), "got {results:?}");
    assert_eq!(results[0].1, 0, "nearest acme chunk is seq 0");

    let pool = sink.pool().await?;
    sqlx::query(&format!(r#"DROP SCHEMA "{db}" CASCADE"#))
        .execute(pool)
        .await?;
    Ok(())
}

#[tokio::test]
async fn promoted_column_filter_returns_only_matching_scope() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_promo";
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let promote = vec![PromoteColumn {
        path: "tenant".into(),
        type_: "text".into(),
    }];
    let sink = PgSink::new(cfg(db, promote), backend, 4);
    sink.create_table().await?;

    let (a, a_embs, b, b_embs) = scoped_corpus();
    sink.write_document("a", &a, &a_embs, &vec![vec![]; 3]).await?;
    sink.write_document("b", &b, &b_embs, &vec![vec![]; 3]).await?;

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let mut f = Filters::default();
    f.columns.insert("tenant".into(), json!("acme"));
    let results = sink.query_top_k_filtered(&q, 10, Some(&f)).await?;

    assert_eq!(results.len(), 3, "all acme rows, no globex rows");
    assert!(results.iter().all(|r| r.0 == "a"), "got {results:?}");

    let pool = sink.pool().await?;
    sqlx::query(&format!(r#"DROP SCHEMA "{db}" CASCADE"#))
        .execute(pool)
        .await?;
    Ok(())
}

/// #75 parity: mirrors the SQLite suite's `tags_filter_returns_only_tagged_rows`
/// so tags-overlap filtering is covered on Postgres too, not just metadata and
/// promoted-column scoping.
#[tokio::test]
async fn tags_filter_returns_only_tagged_rows() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_tags";
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = PgSink::new(cfg(db, vec![]), backend, 4);
    sink.create_table().await?;

    let chunks: Vec<Chunk> = (0..2)
        .map(|i| Chunk {
            doc_id: "d".into(),
            seq_num: i,
            original_content: format!("c{i}"),
            embedded_content: format!("c{i}"),
            metadata: json!({}),
        })
        .collect();
    let embs = vec![vec![1.0, 0.0, 0.0, 0.0], vec![0.99, 0.01, 0.0, 0.0]];
    // seq 0 tagged ["public"], seq 1 tagged ["secret"]. seq 0 is nearer.
    let tags = vec![vec!["public".to_string()], vec!["secret".to_string()]];
    sink.write_document("d", &chunks, &embs, &tags).await?;

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let mut f = Filters::default();
    f.tags.push("SECRET".into()); // case-insensitive overlap

    let results = sink.query_top_k_filtered(&q, 10, Some(&f)).await?;

    assert_eq!(results.len(), 1, "only the secret-tagged row");
    assert_eq!(results[0].1, 1);

    let pool = sink.pool().await?;
    sqlx::query(&format!(r#"DROP SCHEMA "{db}" CASCADE"#))
        .execute(pool)
        .await?;
    Ok(())
}

/// #75 parity: mirrors the SQLite suite's `none_filter_matches_unfiltered` so
/// the no-op-filter guarantee is covered on Postgres too.
#[tokio::test]
async fn none_filter_matches_unfiltered() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_none";
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = PgSink::new(cfg(db, vec![]), backend, 4);
    sink.create_table().await?;

    let chunks: Vec<Chunk> = (0..3)
        .map(|i| Chunk {
            doc_id: "d".into(),
            seq_num: i,
            original_content: format!("c{i}"),
            embedded_content: format!("c{i}"),
            metadata: json!({}),
        })
        .collect();
    let embs = vec![
        vec![1.0, 0.0, 0.0, 0.0],
        vec![0.0, 1.0, 0.0, 0.0],
        vec![0.0, 0.0, 1.0, 0.0],
    ];
    sink.write_document("d", &chunks, &embs, &vec![vec![]; 3]).await?;

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let plain = sink.query_top_k(&q, 2).await?;
    let filtered = sink.query_top_k_filtered(&q, 2, None).await?;
    assert_eq!(plain, filtered);

    let pool = sink.pool().await?;
    sqlx::query(&format!(r#"DROP SCHEMA "{db}" CASCADE"#))
        .execute(pool)
        .await?;
    Ok(())
}
