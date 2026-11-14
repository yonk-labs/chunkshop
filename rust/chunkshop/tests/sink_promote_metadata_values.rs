//! promote_metadata must store the same values the Python sinks store for
//! null, string, int, bool, list and dict across the declared promote types
//! (a JSON null is SQL NULL for every type; text[] takes real arrays).
//! Reference: python/src/chunkshop/sinks/{pg,mariadb,sqlite}.py `_promote_value`.

use chunkshop::backends::{MariadbBackend, PostgresBackend, SQLiteBackend};
use chunkshop::chunker::Chunk;
use chunkshop::config::{
    MariadbTargetConfig, PostgresTargetConfig, PromoteColumn, SqliteTargetConfig,
};
use chunkshop::sinks::{MariadbSink, PgSink, Sink, SqliteSink};
use serde_json::{json, Value};
use sqlx::Row;

const NAME: &str = "chunkshop_review_p1s";
const PG_ENV: &str = "CHUNKSHOP_TEST_DSN";
const MARIA_ENV: &str = "CHUNKSHOP_TEST_DSN_MARIADB";

struct Case {
    ty: &'static str,
    val: Value,
    pg: Option<&'static str>,
    maria: Option<&'static str>,
    sqlite: Option<&'static str>,
}

fn cases() -> Vec<Case> {
    let c = |ty, val, pg, maria, sqlite| Case { ty, val, pg, maria, sqlite };
    vec![
        c("text", Value::Null, None, None, None),
        c("int", Value::Null, None, None, None),
        c("boolean", Value::Null, None, None, None),
        c("jsonb", Value::Null, None, None, None),
        c("text[]", Value::Null, None, None, None),
        c("text", json!("abc"), Some("abc"), Some("abc"), Some("abc")),
        c("int", json!(7), Some("7"), Some("7"), Some("7")),
        c("text", json!(7), Some("7"), Some("7"), Some("7")),
        c("boolean", json!(true), Some("true"), Some("1"), Some("1")),
        c("text[]", json!(["a", "b"]), Some("{a,b}"), Some(r#"["a","b"]"#), Some(r#"["a","b"]"#)),
        c("text[]", json!("en"), Some("{en}"), Some(r#""en""#), Some("en")),
        c(
            "jsonb",
            json!({"k": ["v", 1], "u": "é"}),
            Some(r#"{"k": ["v", 1], "u": "é"}"#),
            Some(r#"{"k":["v",1],"u":"é"}"#),
            Some(r#"{"k":["v",1],"u":"é"}"#),
        ),
        c("jsonb", json!(["a", 2]), Some(r#"["a", 2]"#), Some(r#"["a",2]"#), Some(r#"["a",2]"#)),
        c("jsonb", json!("en"), Some(r#""en""#), Some(r#""en""#), Some("en")),
        // Python PG: json.dumps(sort_keys=True) (default ", " / ": " separators);
        // MariaDB/SQLite: compact separators.
        c(
            "text",
            json!({"b": 1, "a": "é"}),
            Some(r#"{"a": "é", "b": 1}"#),
            Some(r#"{"a":"é","b":1}"#),
            Some(r#"{"a":"é","b":1}"#),
        ),
        c("text", json!(["x", 1]), Some(r#"["x", 1]"#), Some(r#"["x",1]"#), Some(r#"["x",1]"#)),
    ]
}

fn chunk(val: &Value) -> Chunk {
    Chunk {
        doc_id: "d1".into(),
        seq_num: 0,
        original_content: "x".into(),
        embedded_content: "x".into(),
        metadata: json!({ "v": val }),
    }
}

fn promote(ty: &str) -> Vec<PromoteColumn> {
    vec![PromoteColumn { path: "v".into(), type_: ty.into() }]
}

#[tokio::test]
async fn pg_promoted_values() -> anyhow::Result<()> {
    if std::env::var(PG_ENV).is_err() {
        return Ok(());
    }
    let mut bad = vec![];
    for (i, case) in cases().iter().enumerate() {
        let cfg = PostgresTargetConfig {
            dsn_env: PG_ENV.into(),
            database_name: NAME.into(),
            table: format!("t{i}"),
            overwrite: false,
            hnsw: false,
            vector_metric: "cosine".into(),
            mode: "create_if_missing".into(),
            source_tag: None,
            promote_metadata: promote(case.ty),
            force_overwrite: false,
            delete_orphans: false,
            memory: None,
            documents: None,
        };
        let sink = PgSink::new(cfg, PostgresBackend::new(PG_ENV.into()), 4);
        sink.create_table().await?;
        let r = sink
            .write_document("d1", &[chunk(&case.val)], &[vec![0.1; 4]], &[vec![]])
            .await;
        if let Err(e) = r {
            bad.push(format!("{} {} write failed: {e:#}", case.ty, case.val));
            continue;
        }
        let row = sqlx::query(&format!(r#"SELECT v::text AS v FROM "{NAME}"."t{i}""#))
            .fetch_one(sink.pool().await?)
            .await?;
        let got: Option<String> = row.try_get("v")?;
        if got.as_deref() != case.pg {
            bad.push(format!("{} {}: got {got:?}, want {:?}", case.ty, case.val, case.pg));
        }
    }
    let pool = PgSink::new(
        PostgresTargetConfig {
            dsn_env: PG_ENV.into(),
            database_name: NAME.into(),
            table: "t0".into(),
            overwrite: false,
            hnsw: false,
            vector_metric: "cosine".into(),
            mode: "create_if_missing".into(),
            source_tag: None,
            promote_metadata: vec![],
            force_overwrite: false,
            delete_orphans: false,
            memory: None,
            documents: None,
        },
        PostgresBackend::new(PG_ENV.into()),
        4,
    );
    sqlx::query(&format!(r#"DROP SCHEMA IF EXISTS "{NAME}" CASCADE"#))
        .execute(pool.pool().await?)
        .await?;
    assert!(bad.is_empty(), "pg mismatches:\n{}", bad.join("\n"));
    Ok(())
}

#[tokio::test]
async fn mariadb_promoted_values() -> anyhow::Result<()> {
    if std::env::var(MARIA_ENV).is_err() {
        return Ok(());
    }
    let mut bad = vec![];
    let mk = |i: usize, ty: &str| {
        MariadbSink::new(
            MariadbTargetConfig {
                dsn_env: MARIA_ENV.into(),
                database_name: NAME.into(),
                table: format!("t{i}"),
                overwrite: false,
                hnsw: false,
                mode: "create_if_missing".into(),
                source_tag: None,
                promote_metadata: promote(ty),
                force_overwrite: false,
                delete_orphans: false,
                documents: None,
            },
            MariadbBackend::new(MARIA_ENV.into()),
            4,
        )
    };
    for (i, case) in cases().iter().enumerate() {
        let sink = mk(i, case.ty);
        sink.create_table().await?;
        let r = sink
            .write_document("d1", &[chunk(&case.val)], &[vec![0.1; 4]], &[vec![]])
            .await;
        if let Err(e) = r {
            bad.push(format!("{} {} write failed: {e:#}", case.ty, case.val));
            continue;
        }
        let row = sqlx::query(&format!("SELECT CAST(v AS CHAR) AS v FROM `{NAME}`.`t{i}`"))
            .fetch_one(sink.pool().await?)
            .await?;
        let got: Option<String> = row.try_get("v")?;
        if got.as_deref() != case.maria {
            bad.push(format!("{} {}: got {got:?}, want {:?}", case.ty, case.val, case.maria));
        }
    }
    let sink = mk(0, "text");
    sqlx::query(&format!("DROP DATABASE IF EXISTS `{NAME}`"))
        .execute(sink.pool().await?)
        .await?;
    assert!(bad.is_empty(), "mariadb mismatches:\n{}", bad.join("\n"));
    Ok(())
}

#[tokio::test]
async fn sqlite_promoted_values() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let mut bad = vec![];
    for (i, case) in cases().iter().enumerate() {
        let env = format!("CHUNKSHOP_PROMO_VALS_{}_{i}", std::process::id());
        std::env::set_var(&env, dir.path().join(format!("p{i}.db")).to_str().unwrap());
        let sink = SqliteSink::new(
            SqliteTargetConfig {
                dsn_env: env.clone(),
                database_name: "ignored".into(),
                table: "chunks".into(),
                overwrite: false,
                hnsw: false,
                mode: "create_if_missing".into(),
                source_tag: None,
                promote_metadata: promote(case.ty),
                force_overwrite: false,
                delete_orphans: false,
                documents: None,
            },
            SQLiteBackend::new(env.clone()),
            4,
        );
        sink.create_table().await?;
        let r = sink
            .write_document("d1", &[chunk(&case.val)], &[vec![0.1; 4]], &[vec![]])
            .await;
        if let Err(e) = r {
            bad.push(format!("{} {} write failed: {e:#}", case.ty, case.val));
            continue;
        }
        let conn = SQLiteBackend::new(env).connect().await?;
        let g = conn.lock().await;
        let got: Option<String> = g.query_row(
            "SELECT CAST(v AS TEXT) FROM chunks",
            [],
            |r: &rusqlite::Row| r.get(0),
        )?;
        if got.as_deref() != case.sqlite {
            bad.push(format!("{} {}: got {got:?}, want {:?}", case.ty, case.val, case.sqlite));
        }
    }
    assert!(bad.is_empty(), "sqlite mismatches:\n{}", bad.join("\n"));
    Ok(())
}
