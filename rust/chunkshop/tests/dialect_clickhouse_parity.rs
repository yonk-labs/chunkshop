//! Cross-language dialect parity test for ClickHouse. Both Python and Rust
//! assert their BackendDialect impls produce the byte-for-byte outputs in the
//! fixture.

use chunkshop::backends::{BackendDialect, ClickhouseBackend, ColSpec};
use serde_json::Value;

const FIXTURE_PATH: &str = "tests/parity-fixtures/dialect-clickhouse.json";

fn load_fixture() -> Value {
    let raw = std::fs::read_to_string(FIXTURE_PATH).expect("read parity fixture");
    serde_json::from_str(&raw).expect("parse parity fixture")
}

fn backend() -> ClickhouseBackend {
    ClickhouseBackend::new("UNUSED_FOR_DIALECT_PARITY".to_string())
}

#[test]
fn quote_ident_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["quote_ident"].as_array().unwrap() {
        let inp = case["in"].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.quote_ident(inp), expected, "quote_ident({inp:?})");
    }
}

#[test]
fn fq_table_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["fq_table"].as_array().unwrap() {
        let inp = case["in"].as_array().unwrap();
        let db = inp[0].as_str().unwrap();
        let table = inp[1].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.fq_table(db, table), expected, "fq_table({db:?}, {table:?})");
    }
}

#[test]
fn vector_type_ddl_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["vector_type_ddl"].as_array().unwrap() {
        let dim = case["in"].as_u64().unwrap() as usize;
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.vector_type_ddl(dim), expected, "vector_type_ddl({dim})");
    }
}

#[test]
fn json_path_sql_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["json_path_sql"].as_array().unwrap() {
        let inp = case["in"].as_array().unwrap();
        let col = inp[0].as_str().unwrap();
        let path = inp[1].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(
            b.json_path_sql(col, path),
            expected,
            "json_path_sql({col:?}, {path:?})"
        );
    }
}

#[test]
fn upsert_clause_returns_empty_for_clickhouse() {
    let b = backend();
    let f = load_fixture();
    for case in f["upsert_clause"].as_array().unwrap() {
        let inp = &case["in"];
        let keys: Vec<&str> = inp["keys"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
        let updates: Vec<&str> = inp["updates"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(
            b.upsert_clause(&keys, &updates),
            expected,
            "upsert_clause(keys={keys:?}, updates={updates:?}) — CH always returns empty"
        );
    }
}

#[test]
fn create_database_sql_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["create_database_sql"].as_array().unwrap() {
        let inp = case["in"].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.create_database_sql(inp), expected, "create_database_sql({inp:?})");
    }
}

#[test]
fn drop_table_sql_uses_sync_modifier() {
    let b = backend();
    let f = load_fixture();
    for case in f["drop_table_sql"].as_array().unwrap() {
        let inp = case["in"].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(b.drop_table_sql(inp), expected, "drop_table_sql({inp:?})");
    }
}

#[test]
fn add_column_if_not_exists_sql_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["add_column_if_not_exists_sql"].as_array().unwrap() {
        let inp = case["in"].as_array().unwrap();
        let fq = inp[0].as_str().unwrap();
        let col = inp[1].as_str().unwrap();
        let ty = inp[2].as_str().unwrap();
        let expected = case["out"].as_str().unwrap();
        assert_eq!(
            b.add_column_if_not_exists_sql(fq, col, ty),
            expected,
            "add_column_if_not_exists_sql({fq:?}, {col:?}, {ty:?})"
        );
    }
}

#[test]
fn emit_chunks_table_ddl_parity() {
    let b = backend();
    let f = load_fixture();
    for case in f["emit_chunks_table_ddl"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let inp = &case["in"];
        let fq = inp["fq"].as_str().unwrap();
        let cols: Vec<ColSpec> = inp["cols"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| ColSpec {
                name: Box::leak(c["name"].as_str().unwrap().to_string().into_boxed_str()),
                type_ddl: c["type_ddl"].as_str().unwrap().to_string(),
                nullable: false,
                default: None,
                is_primary_key: c["is_primary_key"].as_bool().unwrap(),
            })
            .collect();
        let hnsw = inp["hnsw"].as_bool().unwrap();
        let dim = inp["dim"].as_u64().unwrap() as usize;
        let engine = inp["engine"].as_str();

        let stmts = b.emit_chunks_table_ddl(fq, &cols, hnsw, dim, engine);
        assert_eq!(stmts.len(), 1, "{name}: expected single CREATE TABLE stmt");
        let stmt = &stmts[0];

        for needle in case["out_contains"].as_array().unwrap_or(&vec![]) {
            let n = needle.as_str().unwrap();
            assert!(stmt.contains(n), "{name}: expected fragment {n:?} in:\n{stmt}");
        }
        for excl in case["out_excludes"].as_array().unwrap_or(&vec![]) {
            let e = excl.as_str().unwrap();
            assert!(!stmt.contains(e), "{name}: should NOT contain {e:?}, got:\n{stmt}");
        }
    }
}
