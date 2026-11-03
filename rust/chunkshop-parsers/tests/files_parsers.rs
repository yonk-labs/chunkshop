use chunkshop::config::FilesSourceConfig;
use chunkshop_parsers::ParsedFilesSource;
use tracing_test::traced_test;

fn cfg(glob: String) -> FilesSourceConfig {
    serde_json::from_value(serde_json::json!({ "glob": glob })).unwrap()
}

#[test]
fn text_is_parsed_with_parser_metadata() {
    let d = tempfile::tempdir().unwrap();
    std::fs::write(d.path().join("a.txt"), "hello").unwrap();
    let docs = ParsedFilesSource::new(cfg(format!("{}/*.txt", d.path().display())))
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].content, "hello");
    assert_eq!(docs[0].metadata["parser"], "TextParser");
}

#[test]
fn invalid_utf8_is_decoded_lossily() {
    let d = tempfile::tempdir().unwrap();
    std::fs::write(d.path().join("a.txt"), [b'o', b'k', 0xff]).unwrap();
    let docs = ParsedFilesSource::new(cfg(format!("{}/*.txt", d.path().display())))
        .iter_documents()
        .unwrap();
    assert_eq!(docs[0].content, "ok\u{fffd}");
}

#[test]
#[traced_test]
fn binary_files_are_skipped_not_ingested() {
    let d = tempfile::tempdir().unwrap();
    std::fs::write(d.path().join("good.txt"), "keep").unwrap();
    std::fs::write(d.path().join("bad.bin"), [0u8, 1, 2]).unwrap();
    let docs = ParsedFilesSource::new(cfg(format!("{}/*", d.path().display())))
        .iter_documents()
        .unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].content, "keep");
    assert!(logs_contain("binary content"));
}

#[cfg(not(feature = "pdf"))]
#[test]
fn recognized_format_with_feature_off_fails_loudly() {
    let d = tempfile::tempdir().unwrap();
    std::fs::write(d.path().join("doc.pdf"), b"%PDF-1.4 text").unwrap();
    let err = ParsedFilesSource::new(cfg(format!("{}/*.pdf", d.path().display())))
        .iter_documents()
        .unwrap_err();
    assert!(err.to_string().contains("--features pdf"), "{err}");
}

#[test]
fn empty_glob_errors() {
    let d = tempfile::tempdir().unwrap();
    let r = ParsedFilesSource::new(cfg(format!("{}/*.nope", d.path().display()))).iter_documents();
    assert!(r.is_err());
}
