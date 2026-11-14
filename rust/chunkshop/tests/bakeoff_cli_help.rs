//! PR-021 / RSC-06: `bakeoff --help` documents the explicit `--drop-schema`
//! flag and keeps `--keep-schema` as an accepted no-op.

use std::process::Command;

#[test]
fn bakeoff_help_lists_drop_schema_and_keep_schema() {
    let out = Command::new(env!("CARGO_BIN_EXE_chunkshop-rs"))
        .args(["bakeoff", "--help"])
        .output()
        .expect("run chunkshop-rs");
    let help = String::from_utf8_lossy(&out.stdout);
    assert!(help.contains("--drop-schema"), "missing --drop-schema:\n{help}");
    assert!(help.contains("--keep-schema"), "missing --keep-schema:\n{help}");
}
