//! Integration test for PgTableSource. Boots a temp schema in a real Postgres,
//! INSERTs three rows, runs the source, asserts three Documents come back with
//! the right id / content / title shape. Skips cleanly without DSN.

use std::env;

use chunkshop::config::PgTableSourceConfig;
use chunkshop::source::PgTableSource;

#[tokio::test]
async fn pg_table_source_emits_three_rows() {
    let dsn = match env::var("CHUNKSHOP_TEST_DSN") {
        Ok(v) => v,
        Err(_) => {
            eprintln!("CHUNKSHOP_TEST_DSN not set; skipping pg_table source test");
            return;
        }
    };

    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");

    let schema = "chunkshop_pg_source_test";
    let table = "rows";

    // Cleanup any leftover from a previous run.
    let _ = sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(&pool)
        .await;

    sqlx::query(&format!(r#"CREATE SCHEMA "{schema}""#))
        .execute(&pool)
        .await
        .expect("create schema");

    sqlx::query(&format!(
        r#"
        CREATE TABLE "{schema}"."{table}" (
            doc_id text PRIMARY KEY,
            body text NOT NULL,
            heading text
        )
        "#
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
            r#"INSERT INTO "{schema}"."{table}" (doc_id, body, heading) VALUES ($1, $2, $3)"#
        ))
        .bind(id)
        .bind(body)
        .bind(heading)
        .execute(&pool)
        .await
        .expect("insert row");
    }

    // Use the test DSN so the source can reach the same database.
    env::set_var("CHUNKSHOP_TEST_DSN", &dsn);

    let cfg = PgTableSourceConfig {
        dsn_env: "CHUNKSHOP_TEST_DSN".to_string(),
        schema_name: schema.to_string(),
        table: table.to_string(),
        id_column: "doc_id".to_string(),
        content_column: "body".to_string(),
        title_column: Some("heading".to_string()),
        where_clause: None,
        metadata_columns: vec![],
    };
    let src = PgTableSource::new(cfg);
    let docs = src.iter_documents().await.expect("iter");
    assert_eq!(docs.len(), 3);

    // Order isn't guaranteed without an ORDER BY — sort by id for determinism.
    let mut sorted: Vec<_> = docs.iter().collect();
    sorted.sort_by(|a, b| a.id.cmp(&b.id));
    assert_eq!(sorted[0].id, "alpha");
    assert_eq!(sorted[0].content, "Body alpha.");
    assert_eq!(sorted[0].title.as_deref(), Some("Alpha"));
    assert_eq!(sorted[1].id, "bravo");
    assert_eq!(sorted[1].title.as_deref(), Some("Bravo"));
    assert_eq!(sorted[2].id, "charlie");
    assert_eq!(sorted[2].title, None);

    // Cleanup.
    let _ = sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(&pool)
        .await;
}

#[tokio::test]
async fn pg_table_source_respects_where_clause() {
    let dsn = match env::var("CHUNKSHOP_TEST_DSN") {
        Ok(v) => v,
        Err(_) => {
            eprintln!("CHUNKSHOP_TEST_DSN not set; skipping pg_table where-clause test");
            return;
        }
    };
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");

    let schema = "chunkshop_pg_source_where_test";
    let table = "rows";
    let _ = sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(&pool)
        .await;
    sqlx::query(&format!(r#"CREATE SCHEMA "{schema}""#))
        .execute(&pool)
        .await
        .expect("create schema");
    sqlx::query(&format!(
        r#"CREATE TABLE "{schema}"."{table}" (id text PRIMARY KEY, body text, kind text)"#
    ))
    .execute(&pool)
    .await
    .expect("create table");
    for (id, kind) in [("a", "keep"), ("b", "skip"), ("c", "keep")] {
        sqlx::query(&format!(
            r#"INSERT INTO "{schema}"."{table}" (id, body, kind) VALUES ($1, $2, $3)"#
        ))
        .bind(id)
        .bind(format!("body of {id}"))
        .bind(kind)
        .execute(&pool)
        .await
        .expect("insert");
    }

    env::set_var("CHUNKSHOP_TEST_DSN", &dsn);
    let cfg = PgTableSourceConfig {
        dsn_env: "CHUNKSHOP_TEST_DSN".to_string(),
        schema_name: schema.to_string(),
        table: table.to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: None,
        where_clause: Some("kind = 'keep'".to_string()),
        metadata_columns: vec![],
    };
    let docs = PgTableSource::new(cfg).iter_documents().await.expect("iter");
    assert_eq!(docs.len(), 2, "WHERE kind='keep' should match 2 rows");

    let _ = sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(&pool)
        .await;
}
