//! Integration test: SqliteTableSource iterates over a planted table.

use chunkshop::config::SqliteTableSourceConfig;
use chunkshop::sources::SqliteTableSource;
use tempfile::tempdir;

#[tokio::test]
async fn iter_documents_yields_planted_rows() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("src.db");
    let env = format!("R3_SRC_{}", std::process::id());
    std::env::set_var(&env, path.to_str().unwrap());

    {
        let conn = rusqlite::Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE docs (id TEXT PRIMARY KEY, body TEXT, title TEXT, lang TEXT); \
             INSERT INTO docs VALUES \
                ('a', 'hello world', 'Greeting', 'en'), \
                ('b', 'bonjour le monde', 'Salutation', 'fr')",
        )
        .unwrap();
    }

    let cfg = SqliteTableSourceConfig {
        dsn_env: env,
        database_name: "ignored".into(),
        table: "docs".into(),
        id_column: "id".into(),
        content_column: "body".into(),
        title_column: Some("title".into()),
        where_clause: None,
        metadata_columns: vec!["lang".into()],
    };
    let src = SqliteTableSource::new(cfg);
    let docs = src.iter_documents().await.unwrap();
    assert_eq!(docs.len(), 2);
    let a = docs.iter().find(|d| d.id == "a").unwrap();
    assert_eq!(a.content, "hello world");
    assert_eq!(a.title.as_deref(), Some("Greeting"));
    assert_eq!(a.metadata.get("lang").and_then(|v| v.as_str()), Some("en"));
}

#[tokio::test]
async fn iter_documents_respects_where_clause() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("w.db");
    let env = format!("R3_SRCW_{}", std::process::id());
    std::env::set_var(&env, path.to_str().unwrap());
    {
        let conn = rusqlite::Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE docs (id TEXT, body TEXT, lang TEXT); \
             INSERT INTO docs VALUES ('a', 'x', 'en'), ('b', 'y', 'fr')",
        )
        .unwrap();
    }
    let cfg = SqliteTableSourceConfig {
        dsn_env: env,
        database_name: "ignored".into(),
        table: "docs".into(),
        id_column: "id".into(),
        content_column: "body".into(),
        title_column: None,
        where_clause: Some("lang = 'en'".into()),
        metadata_columns: vec![],
    };
    let docs = SqliteTableSource::new(cfg).iter_documents().await.unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].id, "a");
}
