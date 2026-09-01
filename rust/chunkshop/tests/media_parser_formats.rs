//! Format-by-format round-trip tests for the SP-3 parser layer, plus the
//! guard that adding this layer does not change output for existing
//! text-only YAMLs (SC-13's Rust equivalent).

use std::io::Write;

use chunkshop::config::FilesSourceConfig;
use chunkshop::sources::files::FilesSource;

fn write_temp(dir: &std::path::Path, name: &str, contents: &[u8]) -> std::path::PathBuf {
    let p = dir.join(name);
    let mut f = std::fs::File::create(&p).unwrap();
    f.write_all(contents).unwrap();
    p
}

#[test]
fn text_file_output_is_unchanged_by_the_parser_layer() {
    let dir = tempfile::tempdir().unwrap();
    write_temp(dir.path(), "a.md", b"# Title\n\nBody text.\n");
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.md", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let src = FilesSource::new(cfg);
    let docs = src.iter_documents().unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].content, "# Title\n\nBody text.\n");
    assert_eq!(docs[0].id, "a");
}

#[cfg(feature = "media")]
#[test]
fn pptx_is_refused_not_silently_decoded_as_text() {
    let dir = tempfile::tempdir().unwrap();
    // Not a real PPTX (no crate to generate one with in this test) — a
    // non-UTF-8 byte sequence is the adversarial case that matters: this
    // must NOT come back as a "successful" garbled-text Document.
    write_temp(dir.path(), "deck.pptx", &[0xFF, 0xFE, 0x00, 0x01, 0x02]);
    let cfg = FilesSourceConfig {
        glob: format!("{}/*.pptx", dir.path().display()),
        id_from: "stem".to_string(),
        encoding: "utf-8".to_string(),
    };
    let src = FilesSource::new(cfg);
    let err = src.iter_documents().unwrap_err();
    let msg = format!("{err:#}");
    assert!(
        msg.contains("not yet implemented"),
        "expected a named PPTX refusal, got: {msg}"
    );
}
