//! Cross-language vector parity test for MariaDB. Reads chunks written by the
//! Python sink (via python/scripts/seed_mariadb_cross_lang_fixture.py) and
//! asserts the Rust crate's query_top_k returns the expected ordering.

use std::env;

use chunkshop::backends::MariadbBackend;
use chunkshop::config::MariadbTargetConfig;
use chunkshop::sinks::{MariadbSink, Sink};
use serde_json::Value;

const DSN_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";
const FIXTURE_PATH: &str = "tests/parity-fixtures/mariadb-cross-lang.json";

#[tokio::test]
async fn cross_language_top_k_parity() -> anyhow::Result<()> {
    if env::var(DSN_ENV).is_err() {
        eprintln!("{DSN_ENV} not set; skipping cross-lang parity test");
        return Ok(());
    }

    let raw = std::fs::read_to_string(FIXTURE_PATH).expect("read fixture");
    let f: Value = serde_json::from_str(&raw).expect("parse fixture");
    let dim = f["embed_dim"].as_u64().unwrap() as usize;

    // Verify the table exists. If not, instruct the user to seed it first.
    let pool = sqlx::mysql::MySqlPoolOptions::new()
        .max_connections(1)
        .connect(&env::var(DSN_ENV).unwrap())
        .await?;
    let exists: (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM information_schema.tables \
         WHERE table_schema='chunkshop_xlang' AND table_name='parity'",
    )
    .fetch_one(&pool)
    .await?;
    if exists.0 == 0 {
        panic!(
            "chunkshop_xlang.parity does not exist. Seed it first:\n\
             uv --project python run python python/scripts/seed_mariadb_cross_lang_fixture.py"
        );
    }

    let cfg = MariadbTargetConfig {
        dsn_env: DSN_ENV.to_string(),
        database_name: "chunkshop_xlang".to_string(),
        table: "parity".to_string(),
        overwrite: false,
        hnsw: false,
        mode: "create_if_missing".to_string(),
        source_tag: Some("cross_lang_fixture".to_string()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: false,
    };
    let backend = MariadbBackend::new(DSN_ENV.to_string());
    let sink = MariadbSink::new(cfg, backend, dim);

    let q = &f["queries"][0];
    let qvec: Vec<f32> = q["vec"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_f64().unwrap() as f32)
        .collect();
    let expected: Vec<&str> = q["expected_top_5_ids_in_order"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();

    let results = sink.query_top_k(&qvec, 5).await?;
    assert_eq!(results.len(), 5, "expected top-5 results");

    let actual_ids: Vec<String> = results
        .iter()
        .map(|(doc, seq, _)| format!("{}::{}", doc, seq))
        .collect();

    // Position 0 must be doc-alpha::0 (cos-distance = 0, perfect match).
    assert_eq!(actual_ids[0], expected[0], "top-1 must be alpha");

    // Positions 1-4: orthogonal vectors all have cos-distance = 1.0; ordering
    // among them is implementation-defined. Assert the SET matches.
    use std::collections::BTreeSet;
    let actual_rest: BTreeSet<&str> = actual_ids[1..].iter().map(|s| s.as_str()).collect();
    let expected_rest: BTreeSet<&str> = expected[1..].iter().copied().collect();
    assert_eq!(actual_rest, expected_rest, "remainder set mismatch");

    Ok(())
}
