//! #75: ClickhouseSink::query_top_k_filtered splices an AND-filter WHERE before
//! ORDER BY. Covers JSONExtractString metadata equality (`?`-bound value) and
//! the inlined `hasAny(tags, [...])` array path. Skips when CHUNKSHOP_TEST_DSN_CH
//! is unset.

use chunkshop::backends::ClickhouseBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::TargetConfig;
use chunkshop::sinks::{ClickhouseSink, Filters, Sink};
use serde_json::json;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

fn make_cfg(db: &str) -> chunkshop::config::ClickhouseTargetConfig {
    let yaml = format!(
        "type: clickhouse\ndsn_env: {DSN_ENV}\ndatabase: {db}\ntable: chunks\nmode: overwrite\nhnsw: false\ndelete_orphans: false"
    );
    let raw: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();
    let target: TargetConfig = serde_yaml_ng::from_value(raw).unwrap();
    let TargetConfig::Clickhouse(t) = target else {
        unreachable!("expected Clickhouse variant");
    };
    t
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

async fn drop_db(backend: &ClickhouseBackend, db: &str) -> anyhow::Result<()> {
    let client = backend.client().await?;
    client
        .query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC"))
        .execute()
        .await?;
    Ok(())
}

#[tokio::test]
async fn metadata_filter_returns_only_matching_scope() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_meta";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;
    let sink = ClickhouseSink::new(make_cfg(db), ClickhouseBackend::new(DSN_ENV.to_string()), 4);
    sink.create_table().await?;

    // acme: exact match + two far. globex: one nearer-than-acme's-far chunks.
    let a = vec![mk("a", 0, "acme"), mk("a", 1, "acme")];
    let b = vec![mk("b", 0, "globex")];
    let a_embs = vec![vec![1.0_f32, 0.0, 0.0, 0.0], vec![0.0, 0.0, 1.0, 0.0]];
    let b_embs = vec![vec![0.95_f32, 0.05, 0.0, 0.0]];
    sink.write_document("a", &a, &a_embs, &vec![vec![]; 2]).await?;
    sink.write_document("b", &b, &b_embs, &vec![vec![]; 1]).await?;

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let mut f = Filters::default();
    f.metadata.insert("tenant".into(), json!("acme"));
    let results = sink.query_top_k_filtered(&q, 10, Some(&f)).await?;

    assert_eq!(results.len(), 2, "both acme rows, no globex");
    assert!(results.iter().all(|r| r.0 == "a"), "got {results:?}");
    assert_eq!(results[0].1, 0, "nearest acme chunk is seq 0");

    drop_db(&backend, db).await?;
    Ok(())
}

#[tokio::test]
async fn tags_filter_returns_only_tagged_rows() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_qtkf_tags";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    drop_db(&backend, db).await?;
    let sink = ClickhouseSink::new(make_cfg(db), ClickhouseBackend::new(DSN_ENV.to_string()), 4);
    sink.create_table().await?;

    let chunks = vec![mk("d", 0, "x"), mk("d", 1, "x")];
    let embs = vec![vec![1.0_f32, 0.0, 0.0, 0.0], vec![0.99, 0.01, 0.0, 0.0]];
    // seq 0 nearer but "public"; seq 1 "secret".
    let tags = vec![vec!["public".to_string()], vec!["secret".to_string()]];
    sink.write_document("d", &chunks, &embs, &tags).await?;

    let q = vec![1.0_f32, 0.0, 0.0, 0.0];
    let mut f = Filters::default();
    f.tags.push("SECRET".into()); // case-insensitive; inlined as escaped array
    let results = sink.query_top_k_filtered(&q, 10, Some(&f)).await?;

    assert_eq!(results.len(), 1, "only the secret-tagged row");
    assert_eq!(results[0].1, 1);

    drop_db(&backend, db).await?;
    Ok(())
}
