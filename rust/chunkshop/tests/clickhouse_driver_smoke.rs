//! DC-001 driver gate: prove the official `clickhouse` crate round-trips
//! `Vec<f32>` through `Array(Float32)` cleanly. If this test breaks at the
//! type level, the brief's driver pick must be revisited (fall back to
//! reqwest + JSON) — re-read the mission brief before proceeding.

use clickhouse::{Client, Row};
use serde::{Deserialize, Serialize};

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_CH";

fn skip_if_no_dsn() -> Option<()> {
    if std::env::var(DSN_ENV).is_err() {
        eprintln!("skipping: {DSN_ENV} not set");
        return None;
    }
    Some(())
}

fn client_from_dsn() -> Client {
    // DSN expected in the form clickhouse://user:pass@host:port/database
    // Parse minimal fields. (Full DSN parser lands in Task 2.)
    let dsn = std::env::var(DSN_ENV).unwrap();
    let url = url::Url::parse(&dsn).expect("parse DSN");
    let scheme = if url.scheme().contains("https") { "https" } else { "http" };
    let host_port = format!(
        "{}://{}:{}",
        scheme,
        url.host_str().unwrap(),
        url.port().unwrap_or(8123)
    );
    Client::default()
        .with_url(host_port)
        .with_user(url.username())
        .with_password(url.password().unwrap_or(""))
        .with_database(url.path().trim_start_matches('/'))
}

#[derive(Row, Serialize, Deserialize, Debug, PartialEq)]
struct VecRow {
    id: String,
    v: Vec<f32>,
}

#[tokio::test]
async fn vec_f32_roundtrips_through_array_float32() {
    if skip_if_no_dsn().is_none() {
        return;
    }
    let client = client_from_dsn();

    client
        .query("DROP TABLE IF EXISTS chunkshop_r4_smoke")
        .execute()
        .await
        .expect("drop");

    client
        .query("CREATE TABLE chunkshop_r4_smoke (id String, v Array(Float32)) ENGINE = MergeTree() ORDER BY id")
        .execute()
        .await
        .expect("create");

    let rows = vec![
        VecRow { id: "a".into(), v: vec![0.1_f32, 0.2, -0.3] },
        VecRow { id: "b".into(), v: vec![1.0_f32; 16] },
    ];
    let mut insert = client.insert::<VecRow>("chunkshop_r4_smoke").await.expect("insert");
    for r in &rows {
        insert.write(r).await.expect("write");
    }
    insert.end().await.expect("end");

    let mut cursor = client
        .query("SELECT ?fields FROM chunkshop_r4_smoke ORDER BY id")
        .fetch::<VecRow>()
        .expect("fetch");
    let mut got = Vec::new();
    while let Some(r) = cursor.next().await.expect("cursor") {
        got.push(r);
    }
    assert_eq!(got, rows, "round-trip mismatch — driver pick must be revisited");

    client
        .query("DROP TABLE chunkshop_r4_smoke")
        .execute()
        .await
        .expect("cleanup");
}
