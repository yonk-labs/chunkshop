//! ClickhouseTableSource integration test (SC-010). Round-trips 3 documents
//! through a CH table and asserts Document projections match.

use chunkshop::backends::ClickhouseBackend;
use chunkshop::config::ClickhouseTableSourceConfig;
use chunkshop::sources::ClickhouseTableSource;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

#[tokio::test]
async fn projects_id_content_title_metadata_columns() -> anyhow::Result<()> {
    if skip_if_no_dsn().is_none() {
        return Ok(());
    }
    let backend = ClickhouseBackend::new(DSN_ENV.to_string());
    let client = backend.client().await?;
    let db = "chunkshop_r4_source";

    client
        .query(&format!("DROP DATABASE IF EXISTS `{db}` SYNC"))
        .execute()
        .await?;
    client
        .query(&format!("CREATE DATABASE `{db}`"))
        .execute()
        .await?;
    client
        .query(&format!(
            "CREATE TABLE `{db}`.docs (id String, body String, title String, lang String, author String) ENGINE = MergeTree() ORDER BY id"
        ))
        .execute()
        .await?;
    client
        .query(&format!(
            "INSERT INTO `{db}`.docs VALUES \
             ('a', 'hello world', 'A', 'en', 'alice'), \
             ('b', 'bonjour', 'B', 'fr', 'bob'), \
             ('c', 'hola', 'C', 'es', 'carol')"
        ))
        .execute()
        .await?;

    let cfg = ClickhouseTableSourceConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: db.to_string(),
        table: "docs".to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: Some("title".to_string()),
        where_clause: None,
        metadata_columns: vec!["lang".to_string(), "author".to_string()],
    };
    let source = ClickhouseTableSource::new(cfg);
    let docs = source.iter_documents().await?;
    assert_eq!(docs.len(), 3, "expected 3 docs, got {}", docs.len());
    let by_id: std::collections::HashMap<String, _> =
        docs.iter().map(|d| (d.id.clone(), d)).collect();
    let a = by_id.get("a").unwrap();
    assert_eq!(a.content, "hello world");
    assert_eq!(a.title.as_deref(), Some("A"));
    assert_eq!(a.metadata.get("lang").and_then(|v| v.as_str()), Some("en"));
    assert_eq!(
        a.metadata.get("author").and_then(|v| v.as_str()),
        Some("alice")
    );

    client
        .query(&format!("DROP DATABASE `{db}` SYNC"))
        .execute()
        .await?;
    Ok(())
}
