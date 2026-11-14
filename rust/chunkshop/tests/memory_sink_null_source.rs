//! Parity with Python: MemorySink supersede DELETE and soft-invalidate UPDATE
//! must be NULL-safe on `source`. A cell with no source_tag writes
//! source = NULL; `source = $n` would match nothing.

#![cfg(feature = "memory")]

use chunkshop::backends::PostgresBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::{MemoryConfig, MemoryTier, PostgresTargetConfig, PromoteColumn};
use chunkshop::sinks::base::Sink;
use chunkshop::sinks::MemorySink;
use sqlx::postgres::PgPoolOptions;
use std::time::{SystemTime, UNIX_EPOCH};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

fn promote(path: &str, ty: &str) -> PromoteColumn {
    PromoteColumn {
        path: path.into(),
        type_: ty.into(),
    }
}

fn mk(database: &str, source_tag: Option<&str>, tier: MemoryTier) -> MemorySink {
    let cfg = PostgresTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: database.to_string(),
        table: "memory".to_string(),
        overwrite: false,
        hnsw: false,
        vector_metric: "cosine".to_string(),
        mode: "create_if_missing".to_string(),
        source_tag: source_tag.map(|s| s.to_string()),
        promote_metadata: vec![
            promote("kind", "text"),
            promote("tier", "text"),
            promote("namespace", "text"),
            promote("recorded_at", "timestamptz"),
            promote("session_id", "text"),
            promote("subject", "text"),
            promote("predicate", "text"),
            promote("object", "text"),
            promote("effective_from", "timestamptz"),
            promote("effective_to", "timestamptz"),
            promote("retracted", "boolean"),
            promote("retracted_at", "timestamptz"),
        ],
        force_overwrite: false,
        delete_orphans: false,
        memory: Some(MemoryConfig {
            supersede: matches!(tier, MemoryTier::Consolidated),
            tier,
            namespace: Some("nsp".to_string()),
        }),
        documents: None,
    };
    MemorySink::new(cfg, PostgresBackend::new(DSN_ENV.to_string()), 4)
}

fn chunk(content: &str, meta: serde_json::Value) -> Chunk {
    Chunk {
        doc_id: "s1".to_string(),
        seq_num: 0,
        original_content: content.to_string(),
        embedded_content: content.to_string(),
        metadata: meta,
    }
}

fn db_name(tag: &str) -> String {
    let n = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    format!("chunkshop_review_p1r_{tag}_{n}")
}

async fn admin(dsn: &str) -> anyhow::Result<sqlx::PgPool> {
    Ok(PgPoolOptions::new().max_connections(2).connect(dsn).await?)
}

async fn supersede(source_tag: Option<&str>) -> anyhow::Result<()> {
    let Ok(dsn) = std::env::var(DSN_ENV) else {
        eprintln!("skipping: {DSN_ENV} not set");
        return Ok(());
    };
    let admin_pool = admin(&dsn).await?;
    let database = db_name("sup");
    let emb = vec![0.1, 0.2, 0.3, 0.4];

    let prov = mk(&database, source_tag, MemoryTier::Provisional);
    prov.create_table().await?;
    // Two provisional rows (seq 0 and 1): the consolidated write below only
    // overwrites seq 0 by PK, so seq 1 survives unless the DELETE matches.
    let meta = serde_json::json!({"kind": "episode", "session_id": "s1"});
    let eps = [chunk("prov0", meta.clone()), chunk("prov1", meta.clone())];
    prov.write_document("s1", &eps, &[emb.clone(), emb.clone()], &[vec![], vec![]])
        .await?;

    let cons = mk(&database, source_tag, MemoryTier::Consolidated);
    let ep = chunk("cons", serde_json::json!({"kind": "episode", "session_id": "s1"}));
    cons.write_document("s1", &[ep], &[emb.clone()], &[vec![]]).await?;

    let rows: Vec<(String, String)> = sqlx::query_as(&format!(
        r#"SELECT tier, original_content FROM "{database}".memory WHERE doc_id = 's1' ORDER BY tier"#
    ))
    .fetch_all(&admin_pool)
    .await?;
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{database}" CASCADE"#))
        .execute(&admin_pool)
        .await?;
    assert_eq!(
        rows,
        vec![("consolidated".to_string(), "cons".to_string())],
        "provisional rows must be superseded (source_tag={source_tag:?})"
    );
    Ok(())
}

#[tokio::test]
async fn consolidated_supersedes_provisional_with_source_tag() -> anyhow::Result<()> {
    supersede(Some("ns1")).await
}

#[tokio::test]
async fn consolidated_supersedes_provisional_without_source_tag() -> anyhow::Result<()> {
    supersede(None).await
}

#[tokio::test]
async fn soft_invalidate_retracts_older_fact_without_source_tag() -> anyhow::Result<()> {
    let Ok(dsn) = std::env::var(DSN_ENV) else {
        eprintln!("skipping: {DSN_ENV} not set");
        return Ok(());
    };
    let admin_pool = admin(&dsn).await?;
    let database = db_name("inv");
    let emb = vec![0.1, 0.2, 0.3, 0.4];
    let fact = |obj: &str, ts: &str| {
        chunk(
            obj,
            serde_json::json!({
                "kind": "fact", "session_id": "s1", "subject": "alice",
                "predicate": "lives_in", "object": obj, "effective_from": ts,
            }),
        )
    };
    let sink = mk(&database, None, MemoryTier::Provisional);
    sink.create_table().await?;
    sink.write_document("s1", &[fact("paris", "2026-01-01T00:00:00Z")], &[emb.clone()], &[vec![]])
        .await?;
    sink.write_document("s1", &[fact("rome", "2026-06-01T00:00:00Z")], &[emb.clone()], &[vec![]])
        .await?;

    let rows: Vec<(String, Option<bool>)> = sqlx::query_as(&format!(
        r#"SELECT object, retracted FROM "{database}".memory ORDER BY object"#
    ))
    .fetch_all(&admin_pool)
    .await?;
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{database}" CASCADE"#))
        .execute(&admin_pool)
        .await?;
    assert_eq!(
        rows,
        vec![
            ("paris".to_string(), Some(true)),
            ("rome".to_string(), Some(false)),
        ],
        "older fact must be retracted when source is NULL"
    );
    Ok(())
}
