//! Integration test for MariadbTableSource. Mirrors pg_table_source.rs.
//! Skips when CHUNKSHOP_TEST_DSN_MARIADB is unset.

use std::env;

use chunkshop::config::MariadbTableSourceConfig;
use chunkshop::sources::MariadbTableSource;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";

#[tokio::test]
async fn mariadb_table_source_emits_three_rows() {
    let dsn = match env::var(DSN_ENV) {
        Ok(v) => v,
        Err(_) => {
            eprintln!("{DSN_ENV} not set; skipping");
            return;
        }
    };
    let pool = sqlx::mysql::MySqlPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");

    let database = "chunkshop_mariadb_source_test";
    let table = "rows";
    let _ = sqlx::query(&format!("DROP DATABASE IF EXISTS `{database}`"))
        .execute(&pool)
        .await;
    sqlx::query(&format!("CREATE DATABASE `{database}`"))
        .execute(&pool)
        .await
        .expect("create db");
    sqlx::query(&format!(
        "CREATE TABLE `{database}`.`{table}` (
            doc_id VARCHAR(255) PRIMARY KEY,
            body LONGTEXT NOT NULL,
            heading VARCHAR(255)
         )"
    ))
    .execute(&pool)
    .await
    .expect("create table");

    for (id, body, heading) in [
        ("alpha", "Body alpha.", Some("Alpha")),
        ("bravo", "Body bravo.", Some("Bravo")),
        ("charlie", "Body charlie.", None::<&str>),
    ] {
        sqlx::query(&format!(
            "INSERT INTO `{database}`.`{table}` (doc_id, body, heading) VALUES (?, ?, ?)"
        ))
        .bind(id)
        .bind(body)
        .bind(heading)
        .execute(&pool)
        .await
        .expect("insert");
    }

    let cfg = MariadbTableSourceConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: database.to_string(),
        table: table.to_string(),
        id_column: "doc_id".to_string(),
        content_column: "body".to_string(),
        title_column: Some("heading".to_string()),
        where_clause: None,
        metadata_columns: vec![],
    };
    let docs = MariadbTableSource::new(cfg)
        .iter_documents()
        .await
        .expect("iter");
    assert_eq!(docs.len(), 3);

    let mut sorted: Vec<_> = docs.iter().collect();
    sorted.sort_by(|a, b| a.id.cmp(&b.id));
    assert_eq!(sorted[0].id, "alpha");
    assert_eq!(sorted[0].content, "Body alpha.");
    assert_eq!(sorted[0].title.as_deref(), Some("Alpha"));
    assert_eq!(sorted[1].id, "bravo");
    assert_eq!(sorted[1].title.as_deref(), Some("Bravo"));
    assert_eq!(sorted[2].id, "charlie");
    assert_eq!(sorted[2].title, None);

    let _ = sqlx::query(&format!("DROP DATABASE IF EXISTS `{database}`"))
        .execute(&pool)
        .await;
}
