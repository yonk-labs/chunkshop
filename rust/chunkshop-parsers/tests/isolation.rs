//! `isolate` mode: a hostile file may kill/hang its worker but never the caller.
#![cfg(all(feature = "isolate", feature = "pdf", feature = "docx", feature = "pptx", feature = "xlsx", feature = "odf", feature = "doc"))]
use std::time::{Duration, Instant};

use chunkshop_parsers::{parse, parse_isolated, Isolation, ParseError};

fn worker() -> Isolation {
    Isolation::new(env!("CARGO_BIN_EXE_chunkshop-parsers-worker"))
}

fn fixture(name: &str) -> Vec<u8> {
    std::fs::read(format!("{}/tests/fixtures/{name}", env!("CARGO_MANIFEST_DIR"))).unwrap()
}

#[test]
fn isolated_result_equals_in_process() {
    for f in ["rich.docx", "rich.pptx", "rich.xlsx", "rich.pdf", "rich.odt", "rich.doc"] {
        let ext = f.rsplit('.').next().unwrap();
        let (a, b) = (parse(ext, &fixture(f)), parse_isolated(ext, &fixture(f), &worker()));
        let (a, b) = (a.unwrap_or_else(|e| panic!("{f}: {e}")), b.unwrap_or_else(|e| panic!("{f}: {e}")));
        assert_eq!((a.text, a.parser), (b.text, b.parser), "{f}");
    }
}

#[test]
fn skip_and_feature_errors_cross_the_process_boundary() {
    assert!(matches!(parse_isolated("pdf", &fixture("encrypted.pdf"), &worker()), Err(ParseError::Skip(_))));
    assert!(matches!(parse_isolated("docx", b"not a zip", &worker()), Err(ParseError::Skip(_))));
}

#[test]
fn missing_worker_is_a_skip_not_a_panic() {
    let r = parse_isolated("pdf", b"x", &Isolation::new("/nonexistent/worker"));
    assert!(matches!(r, Err(ParseError::Skip(m)) if m.contains("cannot start worker")));
}

#[test]
fn memory_ceiling_kills_the_worker_not_the_caller() {
    // a docx whose document.xml inflates to ~64 MiB of text, with a 24 MiB worker ceiling
    use std::io::Write;
    let mut z = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    z.start_file("word/document.xml", zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated)).unwrap();
    z.write_all(b"<w:document xmlns:w=\"w\"><w:body><w:p><w:r><w:t>").unwrap();
    let chunk = vec![b'a'; 1 << 20];
    for _ in 0..64 {
        z.write_all(&chunk).unwrap();
    }
    z.write_all(b"</w:t></w:r></w:p></w:body></w:document>").unwrap();
    let bytes = z.finish().unwrap().into_inner();
    let mut iso = worker();
    iso.max_mem_mib = 24;
    let r = parse_isolated("docx", &bytes, &iso);
    // either the parser's own allocation-failure Skip or, failing that, the worker aborting
    assert!(matches!(&r, Err(ParseError::Skip(m)) if m.contains("out of memory") || m.contains("worker died")), "{:?}", r.map(|p| p.text.len()));
    iso.max_mem_mib = 1024; // same file parses fine with the default ceiling
    assert_eq!(parse_isolated("docx", &bytes, &iso).unwrap().text.len(), 64 << 20);
}

#[cfg(unix)]
mod unix {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn script(dir: &tempfile::TempDir, body: &str) -> Isolation {
        let p = dir.path().join("w.sh");
        std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        Isolation::new(p)
    }

    #[test]
    fn hung_worker_is_killed_at_the_timeout() {
        let d = tempfile::tempdir().unwrap();
        let mut iso = script(&d, "sleep 30");
        iso.timeout = Duration::from_millis(300);
        let t = Instant::now();
        let r = parse_isolated("pdf", b"x", &iso);
        assert!(matches!(&r, Err(ParseError::Skip(m)) if m.contains("timed out")), "{:?}", r.map(|p| p.text));
        assert!(t.elapsed() < Duration::from_secs(10));
    }

    #[test]
    fn crashing_worker_is_a_skip() {
        let d = tempfile::tempdir().unwrap();
        let r = parse_isolated("pdf", b"x", &script(&d, "kill -SEGV $$"));
        assert!(matches!(&r, Err(ParseError::Skip(m)) if m.contains("worker died")));
    }

    #[test]
    fn oversized_output_is_a_skip() {
        let d = tempfile::tempdir().unwrap();
        let mut iso = script(&d, "echo Parser; head -c 100000 /dev/zero | tr '\\0' a");
        iso.max_output = 1000;
        assert!(matches!(parse_isolated("pdf", b"x", &iso), Err(ParseError::Skip(_))));
    }
}
