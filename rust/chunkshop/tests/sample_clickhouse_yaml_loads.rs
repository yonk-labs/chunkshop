//! SC-009 smoke: docs/samples/sample-clickhouse.yaml loads cleanly via
//! load_config and resolves to TargetConfig::Clickhouse with the expected fields.
//! Bonus: sample-clickhouse-source.yaml loads as a ClickhouseTable source.

use std::path::PathBuf;

fn workspace_root() -> PathBuf {
    // CARGO_MANIFEST_DIR points to rust/chunkshop/. Workspace root is two up.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}

#[test]
fn sample_clickhouse_yaml_loads_as_clickhouse_target() {
    let path = workspace_root().join("docs/samples/sample-clickhouse.yaml");
    let cfg = chunkshop::load_config(&path).expect("load sample-clickhouse.yaml");
    let chunkshop::config::TargetConfig::Clickhouse(t) = &cfg.target else {
        panic!("expected Clickhouse target, got {:?}", cfg.target);
    };
    assert_eq!(t.database_name, "chunkshop_samples");
    assert_eq!(t.table, "handbook");
    assert_eq!(t.mode, "overwrite");
}

#[test]
fn sample_clickhouse_source_yaml_loads_as_clickhouse_table_source() {
    let path = workspace_root().join("docs/samples/sample-clickhouse-source.yaml");
    let cfg = chunkshop::load_config(&path).expect("load sample-clickhouse-source.yaml");
    let chunkshop::config::SourceConfig::ClickhouseTable(s) = &cfg.source else {
        panic!("expected ClickhouseTable source, got {:?}", cfg.source);
    };
    assert_eq!(s.database_name, "my_app");
    assert_eq!(s.table, "documents");
    assert_eq!(s.id_column, "id");
}
