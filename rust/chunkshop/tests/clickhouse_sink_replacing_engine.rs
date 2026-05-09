//! SC-005 integration test: ReplacingMergeTree(created_at) engine override
//! plus OPTIMIZE FINAL dedup.
//!
//! Same chunks ingested twice on a `ReplacingMergeTree(created_at) ORDER BY (id)`
//! table → 6 rows pre-OPTIMIZE → 3 rows after `OPTIMIZE TABLE … FINAL`.

use chunkshop::backends::ClickhouseBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::TargetConfig;
use chunkshop::sinks::ClickhouseSink;
use serde_json::json;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn replacing_merge_tree_dedups_after_optimize_final() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let db = "chunkshop_r4_replacing";
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    let client = backend.client().await?;
    client
        .query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC"))
        .execute()
        .await?;

    let yaml = format!(
        "type: clickhouse\ndsn_env: {DSN_ENV}\ndatabase: {db}\ntable: chunks\nmode: overwrite\nhnsw: false\nengine: \"ReplacingMergeTree(created_at) ORDER BY (id)\""
    );
    let raw: serde_yml::Value = serde_yml::from_str(&yaml).unwrap();
    let target: TargetConfig = serde_yml::from_value(raw).unwrap();
    let TargetConfig::Clickhouse(cfg) = target else {
        unreachable!("expected Clickhouse variant");
    };

    let sink = ClickhouseSink::new(cfg, ClickhouseBackend::new(DSN_ENV.to_string()), 4);
    sink.create_table_impl().await?;

    // Insert the same 3-chunk doc twice. Without engine override there'd be 6 rows
    // forever (default MergeTree, append-only); with ReplacingMergeTree,
    // OPTIMIZE FINAL collapses them to 3 (latest `created_at` per ORDER BY key).
    let chunks: Vec<Chunk> = (0..3)
        .map(|i| Chunk {
            doc_id: "doc-x".into(),
            seq_num: i,
            original_content: format!("orig {i}"),
            embedded_content: format!("emb {i}"),
            metadata: json!({}),
        })
        .collect();
    let embs: Vec<Vec<f32>> = (0..3).map(|i| vec![i as f32; 4]).collect();
    let tags: Vec<Vec<String>> = (0..3).map(|_| vec![]).collect();

    sink.write_document_impl("doc-x", &chunks, &embs, &tags)
        .await?;
    // Sleep briefly so the second write's `created_at` (now64()) is strictly
    // greater than the first write's. ReplacingMergeTree(created_at) keeps the
    // row with the LATEST `created_at` per ORDER BY key, so distinct timestamps
    // make the dedup deterministic.
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    sink.write_document_impl("doc-x", &chunks, &embs, &tags)
        .await?;

    // Pre-OPTIMIZE: 6 rows (CH does NOT eagerly dedup on INSERT — replacement
    // happens at merge time, which is what the test name asserts).
    #[derive(clickhouse::Row, serde::Deserialize)]
    struct C {
        c: u64,
    }
    let q = format!("SELECT count() AS c FROM `{db}`.chunks");
    let mut cur = client.query(&q).fetch::<C>()?;
    let pre = cur
        .next()
        .await?
        .ok_or_else(|| anyhow::anyhow!("count() returned no rows pre-OPTIMIZE"))?
        .c;
    assert_eq!(pre, 6, "pre-OPTIMIZE: expected 6 rows; got {pre}");

    // Force merge-time dedup.
    client
        .query(&format!("OPTIMIZE TABLE `{db}`.chunks FINAL"))
        .execute()
        .await?;

    let q = format!("SELECT count() AS c FROM `{db}`.chunks FINAL");
    let mut cur = client.query(&q).fetch::<C>()?;
    let post = cur
        .next()
        .await?
        .ok_or_else(|| anyhow::anyhow!("count() returned no rows post-OPTIMIZE"))?
        .c;
    assert_eq!(
        post, 3,
        "post-OPTIMIZE FINAL: expected 3 dedup'd rows; got {post}"
    );

    client
        .query(&format!("DROP DATABASE `{db}` SYNC"))
        .execute()
        .await?;
    Ok(())
}
