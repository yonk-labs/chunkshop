//! Parity with the Python parsers: tests/fixtures/parsers_reference.json is produced by
//! scripts/produce_rust_parser_reference.py. DOCX/PPTX/XLSX must match byte for byte; PDF after
//! whitespace collapse. Failures (corrupt/encrypted/scanned) must be `Skip`, never a panic.
#![allow(dead_code, unused_imports)]
use chunkshop_parsers::{parse, ParseError};
use serde_json::Value;
use sha2::{Digest, Sha256};

fn fixture(name: &str) -> Vec<u8> {
    std::fs::read(format!("{}/tests/fixtures/{name}", env!("CARGO_MANIFEST_DIR"))).unwrap()
}

fn reference(name: &str) -> Value {
    let r: Value = serde_json::from_slice(&fixture("parsers_reference.json")).unwrap();
    let e = r[name].clone();
    assert_eq!(
        e["input_sha256"].as_str().unwrap(),
        format!("{:x}", Sha256::digest(fixture(name))),
        "fixture {name} changed; regenerate the reference"
    );
    e
}

fn ext(name: &str) -> &str {
    name.rsplit('.').next().unwrap()
}

fn exact(name: &str) {
    let e = reference(name);
    let got = parse(ext(name), &fixture(name)).unwrap_or_else(|e| panic!("{name}: {e}"));
    assert_eq!(got.parser, e["parser"].as_str().unwrap());
    assert_eq!(got.text, e["text"].as_str().unwrap(), "{name}");
}

fn skips(name: &str) {
    match parse(ext(name), &fixture(name)) {
        Err(ParseError::Skip(_)) => {}
        other => panic!("{name}: expected Skip, got {:?}", other.map(|p| p.text)),
    }
}

#[cfg(feature = "xlsx")]
#[test]
fn xlsx_exact() {
    exact("rich.xlsx");
}

#[cfg(feature = "docx")]
#[test]
fn docx_exact_and_corrupt_skips() {
    exact("rich.docx");
    skips("truncated.docx");
}

#[cfg(feature = "pptx")]
#[test]
fn pptx_exact() {
    exact("rich.pptx");
}

#[cfg(feature = "pdf")]
#[test]
fn pdf_loose_parity_and_skips() {
    let collapse = |s: &str| s.split_whitespace().collect::<Vec<_>>().join(" ");
    let got = parse("pdf", &fixture("rich.pdf")).unwrap();
    assert_eq!(got.parser, "PDFParser");
    assert_eq!(collapse(&got.text), collapse(reference("rich.pdf")["text"].as_str().unwrap()));
    skips("encrypted.pdf");
    skips("blank.pdf"); // scanned/image-only: Python returns "", Rust skips (recorded divergence)
    skips("truncated.pdf");
}

#[cfg(feature = "docx")]
#[test]
fn zip_bomb_is_skipped() {
    use std::io::Write;
    let mut buf = std::io::Cursor::new(Vec::new());
    let mut z = zip::ZipWriter::new(&mut buf);
    z.start_file("word/document.xml", zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated)).unwrap();
    let chunk = vec![b'a'; 1 << 20];
    for _ in 0..257 {
        z.write_all(&chunk).unwrap(); // 257 MiB > 256 MiB cap, compresses to ~250 KB
    }
    z.finish().unwrap();
    match parse("docx", buf.get_ref()) {
        Err(ParseError::Skip(m)) => assert!(m.contains("zip bomb"), "{m}"),
        other => panic!("expected Skip, got {:?}", other.map(|p| p.text.len())),
    }
}

#[cfg(feature = "html")]
#[test]
fn html_exact() {
    exact("rich.html");
    // script/style/comment contents never leak; unterminated tag doesn't panic
    let t = parse("html", b"<p>a</p><script>if (a<b) {}</script><!-- c --><p>b</p><p").unwrap().text;
    assert_eq!(t, "a\nb");
}

#[cfg(feature = "odf")]
#[test]
fn odf_text_and_slides() {
    let t = parse("odt", &fixture("rich.odt")).unwrap();
    assert_eq!(t.parser, "ODTParser");
    assert_eq!(t.text, "Title\na   b\tc\nd & e\n\nspan end");
    let p = parse("odp", &fixture("rich.odp")).unwrap();
    assert_eq!((p.parser, p.text.as_str()), ("ODPParser", "slide one\nslide two"));
    skips("truncated.odt");
}

#[cfg(feature = "xlsx")]
#[test]
fn ods_and_legacy_excel() {
    let o = parse("ods", &fixture("rich.ods")).unwrap();
    assert_eq!((o.parser, o.text.as_str()), ("ODSParser", "a\tb\n1\t2.5"));
    for ext in ["xls", "xlsb"] {
        assert!(matches!(parse(ext, b"not a workbook"), Err(ParseError::Skip(_))), "{ext}");
    }
}

#[cfg(any(feature = "doc", feature = "ppt"))]
fn ole(streams: &[(&str, &[u8])]) -> Vec<u8> {
    use std::io::Write;
    let mut cf = cfb::CompoundFile::create(std::io::Cursor::new(Vec::new())).unwrap();
    for (n, b) in streams {
        cf.create_stream(format!("/{n}")).unwrap().write_all(b).unwrap();
    }
    cf.into_inner().into_inner()
}

/// rich.doc is a real Word-97 file written by macOS `textutil -convert doc` (scripts/make_doc_fixture.py).
#[cfg(feature = "doc")]
#[test]
fn doc_real_word97_file() {
    let raw = fixture("rich.doc");
    let d = parse("doc", &raw).unwrap();
    assert_eq!(d.parser, "DOCParser");
    assert_eq!(
        d.text,
        "Legacy Title\nFirst paragraph with \u{201c}smart quotes\u{201d} and caf\u{e9} \u{2014} dash.\nSecond\nline break.\nLast paragraph \u{2603} snowman."
    );
    assert!(matches!(parse("doc", b"garbage"), Err(ParseError::Skip(_))));
    // encrypted flag (FIB flags bit 8) -> Skip; the FIB sits at the first sector (offset 512)
    let mut enc = raw.clone();
    let fib = enc.windows(4).position(|w| w == [0xec, 0xa5, 0xc1, 0x00]).unwrap();
    enc[fib + 0x0B] |= 0x01;
    assert!(matches!(parse("doc", &enc), Err(ParseError::Skip(_))));
}

/// Synthetic .ppt (no PowerPoint writer available locally): spec-built records, so this checks
/// our reading of MS-PPT, not real PowerPoint output.
#[cfg(feature = "ppt")]
#[test]
fn ppt_synthetic_records() {
    fn rec(ver_inst: u16, ty: u16, body: &[u8]) -> Vec<u8> {
        let mut v = ver_inst.to_le_bytes().to_vec();
        v.extend(ty.to_le_bytes());
        v.extend((body.len() as u32).to_le_bytes());
        v.extend(body);
        v
    }
    let utf16 = |s: &str| s.encode_utf16().flat_map(u16::to_le_bytes).collect::<Vec<u8>>();
    let slides = [
        rec(0, 0x0F9F, &[0; 4]),
        rec(0, 0x0FA0, &utf16("Slide one\rbullet")),
        rec(0, 0x0F9F, &[0; 4]),
        rec(0, 0x0FA8, b"Slide \x93two\x94"),
    ]
    .concat();
    let notes = [rec(0, 0x0F9F, &[0; 4]), rec(0, 0x0FA0, &utf16("NOTES-SKIPPED"))].concat();
    let doc = [rec(0x00F | (0 << 4), 4080, &slides), rec(0x00F | (2 << 4), 4080, &notes)].concat();
    let stream = rec(0x000F, 1000, &doc);
    let p = parse("ppt", &ole(&[("PowerPoint Document", &stream)])).unwrap();
    assert_eq!((p.parser, p.text.as_str()), ("PPTParser", "Slide one\nbullet\nSlide \u{201c}two\u{201d}"));
    assert!(matches!(parse("ppt", &ole(&[("PowerPoint Document", &rec(0xF, 1000, &[]))])), Err(ParseError::Skip(_))));
}
