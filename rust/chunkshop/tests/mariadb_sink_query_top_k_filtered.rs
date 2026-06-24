//! #75: MariadbSink::query_top_k_filtered splices an AND-filter WHERE before
//! ORDER BY. Covers JSON_EXTRACT metadata equality, JSON_OVERLAPS tags, and
//! CAST-based promoted-column equality. Skips when CHUNKSHOP_TEST_DSN_MARIADB
//! is unset.

use chunkshop::backends::MariadbBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::{MariadbTargetConfig, PromoteColumn};
use chunkshop::sinks::{Filters, MariadbSink, Sink};
use serde_json::json;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

fn cfg(db: &str, promote: Vec<PromoteColumn>) -> MariadbTargetConfig {
    MariadbTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: db.to_string(),
        table: "chunks".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".to_string(),
        source_tag: Some("t1".to_string()),
        promote_metadata: promote,
        force_overwrite: false,
        delete_orphans: false,
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

// A "globex" chunk is nearer to the query than two "acme" chunks, so the filter
// (not just ranking) is what excludes it.
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

async fn drop_db(sink: &MariadbSink, db: &str) -> anyhow::Result<()> {
    let pool = sink.pool().await?;
    sqlx::query(&format!("DROP DATABASE IF EXISTS `{db}`"))
        .execute(pool)
        .await?;
    Ok(())
}

#[tokio::test]
async fn metadata_filter_returns_only_matching_scope() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_meta";
    let sink = MariadbSink::new(cfg(db, vec![]), MariadbBackend::new(DSN_ENV.to_string()), 4);
    drop_db(&sink, db).await?;
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

    drop_db(&sink, db).await?;
    Ok(())
}

#[tokio::test]
async fn tags_filter_returns_only_tagged_rows() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_tags";
    let sink = MariadbSink::new(cfg(db, vec![]), MariadbBackend::new(DSN_ENV.to_string()), 4);
    drop_db(&sink, db).await?;
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
    // seq 0 nearer but tagged "public"; seq 1 tagged "secret".
    let tags = vec![vec!["public".to_string()], vec!["secret".to_string()]];
    sink.write_document("d", &chunks, &embs, &tags).await?;

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let mut f = Filters::default();
    f.tags.push("SECRET".into()); // case-insensitive
    let results = sink.query_top_k_filtered(&q, 10, Some(&f)).await?;

    assert_eq!(results.len(), 1, "only the secret-tagged row");
    assert_eq!(results[0].1, 1);

    drop_db(&sink, db).await?;
    Ok(())
}

#[tokio::test]
async fn promoted_column_filter_returns_only_matching_scope() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_promo";
    let promote = vec![PromoteColumn {
        path: "tenant".into(),
        type_: "text".into(),
    }];
    let sink = MariadbSink::new(cfg(db, promote), MariadbBackend::new(DSN_ENV.to_string()), 4);
    drop_db(&sink, db).await?;
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

    drop_db(&sink, db).await?;
    Ok(())
}
