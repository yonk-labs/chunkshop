//! RM-B Task 2: pg_table tuple cursor `{after_ts, after_id}` boundary-row safety.
//!
//! Mirrors `python/tests/chunkshop/test_pg_table_incremental.py::
//! test_pg_table_handles_row_inserted_at_cursor_boundary`. The Python fix
//! (commit `ff01268`) replaced a `> %s` single-column cursor with a tuple
//! cursor (`(updated_at, id::text) > (%s, %s)`), which defends against
//! silent row loss when two rows commit at the same boundary timestamp.
//!
//! This test seeds row `c1@T`, advances the cursor past it, then inserts
//! `c2@T` (same timestamp) and asserts the second sync emits `c2`. Without
//! the tuple-cursor fix this would emit nothing — silent data loss.
//!
//! Skips cleanly when `$CHUNKSHOP_TEST_DSN` is not set.

use std::env;

use chunkshop::config::PgTableSourceConfig;
use chunkshop::sources::base::IncrementalSource;
use chunkshop::sources::PgTableSource;

#[tokio::test]
async fn pg_table_handles_row_inserted_at_cursor_boundary() {
    let dsn = match env::var("CHUNKSHOP_TEST_DSN") {
        Ok(v) => v,
        Err(_) => {
            eprintln!("CHUNKSHOP_TEST_DSN not set; skipping pg_table_tuple_cursor test");
            return;
        }
    };

    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");

    let schema = "chunkshop_pg_tuple_cursor_test";
    let table = "rows";

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
            id text PRIMARY KEY,
            body text NOT NULL,
            updated_at timestamptz NOT NULL
        )
        "#
    ))
    .execute(&pool)
    .await
    .expect("create table");

    // Boundary timestamp — both rows will share this `updated_at`.
    let boundary = "2026-05-25 12:00:00+00";

    sqlx::query(&format!(
        r#"INSERT INTO "{schema}"."{table}" (id, body, updated_at) VALUES ($1, $2, $3::timestamptz)"#
    ))
    .bind("c1")
    .bind("body c1")
    .bind(boundary)
    .execute(&pool)
    .await
    .expect("insert c1");

    env::set_var("CHUNKSHOP_TEST_DSN", &dsn);

    let cfg = PgTableSourceConfig {
        dsn_env: "CHUNKSHOP_TEST_DSN".to_string(),
        schema_name: schema.to_string(),
        table: table.to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: None,
        where_clause: None,
        metadata_columns: vec![],
        updated_at_column: Some("updated_at".to_string()),
    };
    let src = PgTableSource::new(cfg);

    // First sync: empty cursor → must emit c1.
    let cursor0 = src.empty_cursor();
    let docs1 = src
        .iter_changes_since(&cursor0)
        .await
        .expect("first iter_changes_since");
    assert_eq!(docs1.len(), 1);
    assert_eq!(docs1[0].id, "c1");
    assert!(
        docs1[0].metadata.get("_updated_at").is_some(),
        "emitted doc must carry _updated_at in metadata for cursor_from to work"
    );

    // Advance the cursor to "after c1".
    let cursor1 = src.cursor_from(&docs1[0]);
    assert_eq!(cursor1.after_id.as_deref(), Some("c1"));
    assert!(
        cursor1.after_ts.is_some(),
        "cursor_from must populate after_ts from last doc's _updated_at"
    );

    // Now insert c2 at the SAME timestamp as c1. A naive `updated_at > ?`
    // cursor would miss this. The tuple cursor (updated_at, id::text) catches
    // it because "c2" > "c1" lexicographically at the same timestamp.
    sqlx::query(&format!(
        r#"INSERT INTO "{schema}"."{table}" (id, body, updated_at) VALUES ($1, $2, $3::timestamptz)"#
    ))
    .bind("c2")
    .bind("body c2")
    .bind(boundary)
    .execute(&pool)
    .await
    .expect("insert c2 at boundary");

    // Second sync: cursor1 → must emit c2 (and only c2; not c1 again).
    let docs2 = src
        .iter_changes_since(&cursor1)
        .await
        .expect("second iter_changes_since");
    let ids: Vec<&str> = docs2.iter().map(|d| d.id.as_str()).collect();
    assert_eq!(
        ids,
        vec!["c2"],
        "second sync at cursor must emit c2 (boundary-row protection); got {ids:?}"
    );

    // Cleanup
    let _ = sqlx::query(&format!(r#"DROP SCHEMA "{schema}" CASCADE"#))
        .execute(&pool)
        .await;
}

#[tokio::test]
async fn pg_table_empty_cursor_emits_all_existing_rows_in_canonical_order() {
    let dsn = match env::var("CHUNKSHOP_TEST_DSN") {
        Ok(v) => v,
        Err(_) => return,
    };

    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");

    let schema = "chunkshop_pg_tuple_cursor_canon";
    let table = "rows";

    let _ = sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{schema}" CASCADE"#))
        .execute(&pool)
        .await;
    sqlx::query(&format!(r#"CREATE SCHEMA "{schema}""#))
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query(&format!(
        r#"CREATE TABLE "{schema}"."{table}" (id text PRIMARY KEY, body text NOT NULL, updated_at timestamptz NOT NULL)"#
    ))
    .execute(&pool)
    .await
    .unwrap();

    // Insert out of order; canonical order is (updated_at, id::text).
    for (id, ts) in [
        ("b", "2026-05-25 12:00:01+00"),
        ("a", "2026-05-25 12:00:00+00"),
        ("c", "2026-05-25 12:00:02+00"),
    ] {
        sqlx::query(&format!(
            r#"INSERT INTO "{schema}"."{table}" (id, body, updated_at) VALUES ($1, $2, $3::timestamptz)"#
        ))
        .bind(id)
        .bind(format!("body {id}"))
        .bind(ts)
        .execute(&pool)
        .await
        .unwrap();
    }

    env::set_var("CHUNKSHOP_TEST_DSN", &dsn);
    let cfg = PgTableSourceConfig {
        dsn_env: "CHUNKSHOP_TEST_DSN".to_string(),
        schema_name: schema.to_string(),
        table: table.to_string(),
        id_column: "id".to_string(),
        content_column: "body".to_string(),
        title_column: None,
        where_clause: None,
        metadata_columns: vec![],
        updated_at_column: Some("updated_at".to_string()),
    };
    let src = PgTableSource::new(cfg);
    let docs = src.iter_changes_since(&src.empty_cursor()).await.unwrap();
    let ids: Vec<&str> = docs.iter().map(|d| d.id.as_str()).collect();
    assert_eq!(ids, vec!["a", "b", "c"], "ascending (updated_at, id) order");

    let _ = sqlx::query(&format!(r#"DROP SCHEMA "{schema}" CASCADE"#))
        .execute(&pool)
        .await;
}
