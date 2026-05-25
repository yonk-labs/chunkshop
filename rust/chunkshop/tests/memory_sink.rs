//! RM-A Task 9: MemorySink integration tests against real PG.
//! Tier/namespace/recorded_at stamping + namespace-qualified row id
//! (Python parity fix 3dbd12f). Task 10 will add supersede + soft-invalidate.

#![cfg(feature = "memory")]

use chunkshop::backends::PostgresBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::{MemoryConfig, MemoryTier, PostgresTargetConfig};
use chunkshop::sinks::base::Sink;
use chunkshop::sinks::MemorySink;
use sqlx::postgres::PgPoolOptions;
use sqlx::Row;
use std::time::{SystemTime, UNIX_EPOCH};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN";

fn skip_if_no_dsn() -> Option<String> {
    match std::env::var(DSN_ENV) {
        Ok(v) if !v.is_empty() => Some(v),
        _ => {
            eprintln!("skipping: {DSN_ENV} not set");
            None
        }
    }
}

fn unique_database() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    format!("chunkshop_test_mem_sink_{nanos}")
}

fn mk_cfg(database: &str, source_tag: &str, namespace: Option<&str>) -> PostgresTargetConfig {
    PostgresTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: database.to_string(),
        table: "memory".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "create_if_missing".to_string(),
        source_tag: Some(source_tag.to_string()),
        promote_metadata: vec![
            // The promote columns SP-A presets declare. MemorySink stamps
            // tier/namespace/recorded_at; promote_metadata surfaces them
            // as typed columns.
            chunkshop::config::PromoteColumn {
                path: "kind".into(),
                type_: "text".into(),
            },
            chunkshop::config::PromoteColumn {
                path: "tier".into(),
                type_: "text".into(),
            },
            chunkshop::config::PromoteColumn {
                path: "namespace".into(),
                type_: "text".into(),
            },
            chunkshop::config::PromoteColumn {
                path: "recorded_at".into(),
                type_: "timestamptz".into(),
            },
            chunkshop::config::PromoteColumn {
                path: "session_id".into(),
                type_: "text".into(),
            },
            chunkshop::config::PromoteColumn {
                path: "subject".into(),
                type_: "text".into(),
            },
            chunkshop::config::PromoteColumn {
                path: "predicate".into(),
                type_: "text".into(),
            },
            chunkshop::config::PromoteColumn {
                path: "object".into(),
                type_: "text".into(),
            },
        ],
        force_overwrite: false,
        delete_orphans: false,
        memory: Some(MemoryConfig {
            tier: MemoryTier::Consolidated,
            supersede: true,
            namespace: namespace.map(|s| s.to_string()),
        }),
        documents: None,
    }
}

async fn cleanup(admin_pool: &sqlx::PgPool, database: &str) -> anyhow::Result<()> {
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{database}" CASCADE"#))
        .execute(admin_pool)
        .await?;
    Ok(())
}

fn chunk(doc_id: &str, seq_num: usize, content: &str, kind: &str, session_id: &str) -> Chunk {
    Chunk {
        doc_id: doc_id.to_string(),
        seq_num,
        original_content: content.to_string(),
        embedded_content: content.to_string(),
        metadata: serde_json::json!({
            "kind": kind,
            "session_id": session_id,
            "episode_end_ts": 1_767_225_600.0_f64,
        }),
    }
}

#[tokio::test]
async fn create_table_includes_canonical_plus_promoted_columns() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    let cfg = mk_cfg(&database, "ns1", Some("ns1"));
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = MemorySink::new(cfg, backend, 4);
    sink.create_table().await?;

    let cols = sqlx::query_scalar::<_, String>(
        "SELECT column_name FROM information_schema.columns \
         WHERE table_schema = $1 AND table_name = 'memory' ORDER BY column_name",
    )
    .bind(&database)
    .fetch_all(&admin_pool)
    .await?;
    // Canonical chunkshop columns
    for c in &[
        "id",
        "doc_id",
        "seq_num",
        "original_content",
        "embedded_content",
        "tags",
        "metadata",
        "embedding",
        "source",
        "created_at",
    ] {
        assert!(cols.contains(&c.to_string()), "missing canonical column {c}");
    }
    // Promote columns the memory presets declare
    for c in &[
        "kind",
        "tier",
        "namespace",
        "recorded_at",
        "session_id",
        "subject",
        "predicate",
        "object",
    ] {
        assert!(cols.contains(&c.to_string()), "missing promoted column {c}");
    }

    cleanup(&admin_pool, &database).await?;
    Ok(())
}

#[tokio::test]
async fn write_stamps_tier_namespace_recorded_at_and_kind() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    let cfg = mk_cfg(&database, "ns1", Some("ns1"));
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = MemorySink::new(cfg, backend, 4);
    sink.create_table().await?;

    let ch = chunk("s1", 0, "episode body", "episode", "s1");
    sink.write_document(
        "s1",
        std::slice::from_ref(&ch),
        &[vec![0.1, 0.2, 0.3, 0.4]],
        &[vec![]],
    )
    .await?;

    let row = sqlx::query(&format!(
        r#"SELECT id, tier, namespace, recorded_at IS NOT NULL AS has_recorded, kind, session_id
           FROM "{database}".memory WHERE doc_id = 's1' AND seq_num = 0"#,
    ))
    .fetch_one(&admin_pool)
    .await?;
    let id: String = row.try_get("id")?;
    let tier: String = row.try_get("tier")?;
    let namespace: String = row.try_get("namespace")?;
    let has_recorded: bool = row.try_get("has_recorded")?;
    let kind: String = row.try_get("kind")?;
    let session_id: String = row.try_get("session_id")?;

    // Python 3dbd12f: row id is namespace-qualified `{ns}::{doc_id}::{seq_num}`.
    assert_eq!(id, "ns1::s1::0", "row id must be namespace-qualified");
    assert_eq!(tier, "consolidated");
    assert_eq!(namespace, "ns1");
    assert!(has_recorded, "recorded_at must be stamped");
    assert_eq!(kind, "episode");
    assert_eq!(session_id, "s1");

    cleanup(&admin_pool, &database).await?;
    Ok(())
}

#[tokio::test]
async fn namespace_falls_back_to_source_tag_when_not_explicit() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    // memory.namespace=None → should fall back to source_tag ("ns_via_tag").
    let cfg = mk_cfg(&database, "ns_via_tag", None);
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = MemorySink::new(cfg, backend, 4);
    sink.create_table().await?;

    let ch = chunk("s1", 0, "body", "episode", "s1");
    sink.write_document(
        "s1",
        std::slice::from_ref(&ch),
        &[vec![0.1, 0.2, 0.3, 0.4]],
        &[vec![]],
    )
    .await?;

    let ns: String = sqlx::query_scalar(&format!(
        r#"SELECT namespace FROM "{database}".memory WHERE doc_id = 's1'"#
    ))
    .fetch_one(&admin_pool)
    .await?;
    assert_eq!(ns, "ns_via_tag");
    let id: String = sqlx::query_scalar(&format!(
        r#"SELECT id FROM "{database}".memory WHERE doc_id = 's1'"#
    ))
    .fetch_one(&admin_pool)
    .await?;
    assert_eq!(id, "ns_via_tag::s1::0");

    cleanup(&admin_pool, &database).await?;
    Ok(())
}

// --- RM-A Task 10: supersede + soft-invalidate ---------------------------

fn fact_chunk(
    doc_id: &str,
    seq_num: usize,
    subject: &str,
    predicate: &str,
    object: &str,
    effective_from_iso: &str,
) -> Chunk {
    Chunk {
        doc_id: doc_id.to_string(),
        seq_num,
        original_content: format!("{subject} {predicate} {object}"),
        embedded_content: format!("{subject} {predicate} {object}"),
        metadata: serde_json::json!({
            "kind": "fact",
            "session_id": doc_id,
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "effective_from": effective_from_iso,
        }),
    }
}

fn mk_provisional(database: &str, source_tag: &str) -> PostgresTargetConfig {
    let mut c = mk_cfg(database, source_tag, Some(source_tag));
    if let Some(m) = c.memory.as_mut() {
        m.tier = MemoryTier::Provisional;
        m.supersede = false;
    }
    // Also need `object` + `effective_from` + `retracted` + `retracted_at`
    // + `effective_to` promote columns for the soft-invalidate test.
    c.promote_metadata.push(chunkshop::config::PromoteColumn {
        path: "effective_from".into(),
        type_: "timestamptz".into(),
    });
    c.promote_metadata.push(chunkshop::config::PromoteColumn {
        path: "effective_to".into(),
        type_: "timestamptz".into(),
    });
    c.promote_metadata.push(chunkshop::config::PromoteColumn {
        path: "retracted".into(),
        type_: "boolean".into(),
    });
    c.promote_metadata.push(chunkshop::config::PromoteColumn {
        path: "retracted_at".into(),
        type_: "timestamptz".into(),
    });
    c
}

fn mk_consolidated_with_promote(database: &str, source_tag: &str) -> PostgresTargetConfig {
    let mut c = mk_provisional(database, source_tag);
    if let Some(m) = c.memory.as_mut() {
        m.tier = MemoryTier::Consolidated;
        m.supersede = true;
    }
    c
}

#[tokio::test]
async fn consolidated_supersedes_provisional_scoped_by_source() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    let backend = || PostgresBackend::new(DSN_ENV.to_string());

    // ns1 provisional write
    let prov_ns1 = MemorySink::new(mk_provisional(&database, "ns1"), backend(), 4);
    prov_ns1.create_table().await?;
    prov_ns1
        .write_document("s1", &[chunk("s1", 0, "ns1 provisional", "episode", "s1")],
                         &[vec![0.0; 4]], &[vec![]])
        .await?;
    // ns2 provisional write (same session) — different source_tag.
    let prov_ns2 = MemorySink::new(mk_provisional(&database, "ns2"), backend(), 4);
    prov_ns2
        .write_document("s1", &[chunk("s1", 0, "ns2 provisional", "episode", "s1")],
                         &[vec![0.0; 4]], &[vec![]])
        .await?;
    // ns1 consolidated write — must DELETE ns1 provisional but leave ns2 alone.
    let cons_ns1 = MemorySink::new(mk_consolidated_with_promote(&database, "ns1"), backend(), 4);
    cons_ns1
        .write_document("s1", &[chunk("s1", 0, "ns1 consolidated", "episode", "s1")],
                         &[vec![0.0; 4]], &[vec![]])
        .await?;

    let rows: Vec<(String, String)> = sqlx::query_as(&format!(
        r#"SELECT source, tier FROM "{database}".memory WHERE doc_id = 's1' ORDER BY source, tier"#
    ))
    .fetch_all(&admin_pool)
    .await?;
    // Expected: ("ns1","consolidated") + ("ns2","provisional"). ns1 provisional gone.
    let by_source_tier: Vec<(String, String)> = rows.into_iter().collect();
    assert!(
        by_source_tier.contains(&("ns1".into(), "consolidated".into())),
        "ns1 consolidated missing: {by_source_tier:?}"
    );
    assert!(
        by_source_tier.contains(&("ns2".into(), "provisional".into())),
        "ns2 provisional must survive (different source): {by_source_tier:?}"
    );
    assert!(
        !by_source_tier.contains(&("ns1".into(), "provisional".into())),
        "ns1 provisional must be superseded: {by_source_tier:?}"
    );

    cleanup(&admin_pool, &database).await?;
    Ok(())
}

#[tokio::test]
async fn double_consolidate_is_idempotent() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    let backend = || PostgresBackend::new(DSN_ENV.to_string());

    // Two separate MemorySink instances (each gets a fresh `_superseded` set,
    // so each will run the DELETE). Result must still be exactly 1 row.
    let c1 = MemorySink::new(mk_consolidated_with_promote(&database, "ns1"), backend(), 4);
    c1.create_table().await?;
    c1.write_document("s1", &[chunk("s1", 0, "v1", "episode", "s1")],
                       &[vec![0.0; 4]], &[vec![]])
        .await?;
    let c2 = MemorySink::new(mk_consolidated_with_promote(&database, "ns1"), backend(), 4);
    c2.write_document("s1", &[chunk("s1", 0, "v2", "episode", "s1")],
                       &[vec![0.0; 4]], &[vec![]])
        .await?;

    let n: i64 = sqlx::query_scalar(&format!(
        r#"SELECT count(*) FROM "{database}".memory WHERE doc_id = 's1'"#
    ))
    .fetch_one(&admin_pool)
    .await?;
    assert_eq!(n, 1, "double-consolidate must converge to 1 row, got {n}");

    let content: String = sqlx::query_scalar(&format!(
        r#"SELECT original_content FROM "{database}".memory WHERE doc_id = 's1'"#
    ))
    .fetch_one(&admin_pool)
    .await?;
    assert_eq!(content, "v2", "second write wins after supersede");

    cleanup(&admin_pool, &database).await?;
    Ok(())
}

#[tokio::test]
async fn soft_invalidate_retracts_older_contradicting_fact() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    let backend = || PostgresBackend::new(DSN_ENV.to_string());

    let sink = MemorySink::new(mk_consolidated_with_promote(&database, "ns1"), backend(), 4);
    sink.create_table().await?;

    // Old fact: queue uses redis, effective_from 2026-01-01.
    sink.write_document(
        "s1",
        &[fact_chunk("s1", 0, "queue", "uses", "redis",
                     "2026-01-01T00:00:00+00:00")],
        &[vec![0.0; 4]],
        &[vec![]],
    )
    .await?;
    // Newer contradicting fact: queue uses postgres, effective_from 2026-03-01.
    sink.write_document(
        "s2",
        &[fact_chunk("s2", 0, "queue", "uses", "postgres",
                     "2026-03-01T00:00:00+00:00")],
        &[vec![0.0; 4]],
        &[vec![]],
    )
    .await?;

    let rows: Vec<(String, bool, Option<String>)> = sqlx::query_as(&format!(
        r#"SELECT object, retracted, effective_to::text
           FROM "{database}".memory
           WHERE kind = 'fact'
           ORDER BY effective_from"#
    ))
    .fetch_all(&admin_pool)
    .await?;
    assert_eq!(rows.len(), 2, "expected 2 fact rows; got {rows:?}");
    let (obj0, ret0, eff_to0) = &rows[0];
    let (obj1, ret1, _eff_to1) = &rows[1];
    assert_eq!(obj0, "redis");
    assert!(*ret0, "older redis fact must be retracted");
    assert!(eff_to0.as_ref().map(|s| s.starts_with("2026-03-01")).unwrap_or(false),
            "effective_to on retracted row should be the newer effective_from: {eff_to0:?}");
    assert_eq!(obj1, "postgres");
    assert!(!*ret1, "current postgres fact must not be retracted");

    cleanup(&admin_pool, &database).await?;
    Ok(())
}

#[tokio::test]
async fn sparse_triple_is_no_op_on_invalidate() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    let backend = || PostgresBackend::new(DSN_ENV.to_string());

    let sink = MemorySink::new(mk_consolidated_with_promote(&database, "ns1"), backend(), 4);
    sink.create_table().await?;

    // Fact chunk missing subject — the soft-invalidate path must skip it
    // entirely (no UPDATE, no panic). Insert should still succeed.
    let mut sparse = chunk("s1", 0, "no subject", "fact", "s1");
    sparse.metadata = serde_json::json!({
        "kind": "fact",
        "session_id": "s1",
        // intentionally no subject / predicate / object / effective_from
    });
    sink.write_document(
        "s1",
        std::slice::from_ref(&sparse),
        &[vec![0.0; 4]],
        &[vec![]],
    )
    .await?;
    let n: i64 = sqlx::query_scalar(&format!(
        r#"SELECT count(*) FROM "{database}".memory"#
    ))
    .fetch_one(&admin_pool)
    .await?;
    assert_eq!(n, 1, "sparse fact must still insert; invalidate must skip");

    cleanup(&admin_pool, &database).await?;
    Ok(())
}

#[tokio::test]
async fn underscore_prefixed_metadata_keys_stripped() -> anyhow::Result<()> {
    let Some(dsn) = skip_if_no_dsn() else { return Ok(()); };
    let admin_pool = PgPoolOptions::new().max_connections(2).connect(&dsn).await?;
    let database = unique_database();
    let cfg = mk_cfg(&database, "ns1", Some("ns1"));
    let backend = PostgresBackend::new(DSN_ENV.to_string());
    let sink = MemorySink::new(cfg, backend, 4);
    sink.create_table().await?;

    // Chunk metadata carries an underscore-prefixed transient key
    // (`_episode_events`) — must NOT survive into the persisted jsonb.
    let mut ch = chunk("s1", 0, "body", "episode", "s1");
    ch.metadata
        .as_object_mut()
        .unwrap()
        .insert("_episode_events".into(), serde_json::json!([{"role":"x"}]));
    sink.write_document(
        "s1",
        std::slice::from_ref(&ch),
        &[vec![0.0, 0.0, 0.0, 0.0]],
        &[vec![]],
    )
    .await?;

    let meta: serde_json::Value = sqlx::query_scalar(&format!(
        r#"SELECT metadata FROM "{database}".memory WHERE doc_id = 's1'"#
    ))
    .fetch_one(&admin_pool)
    .await?;
    assert!(
        meta.get("_episode_events").is_none(),
        "underscore-prefixed transient keys must be stripped before insert: {:?}",
        meta
    );

    cleanup(&admin_pool, &database).await?;
    Ok(())
}
