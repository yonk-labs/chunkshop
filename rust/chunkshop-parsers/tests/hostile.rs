#![allow(unused_imports)]
//! Regression tests for hostile / malformed input found in the security review.
//! Each must return promptly with Skip or sane text, never abort or hang.
use chunkshop_parsers::{parse, ParseError};

#[cfg(feature = "ppt")]
fn ole(stream: &str, b: &[u8]) -> Vec<u8> {
    use std::io::Write;
    let mut cf = cfb::CompoundFile::create(std::io::Cursor::new(Vec::new())).unwrap();
    cf.create_stream(format!("/{stream}")).unwrap().write_all(b).unwrap();
    cf.into_inner().into_inner()
}

#[cfg(feature = "ppt")]
#[test]
fn ppt_deeply_nested_containers_do_not_overflow_the_stack() {
    // 200k nested SlideListWithText containers, each wrapping the rest.
    let n = 200_000usize;
    let mut s = Vec::with_capacity(n * 8);
    for i in 0..n {
        let remaining = ((n - i - 1) * 8) as u32;
        s.extend(0x000Fu16.to_le_bytes());
        s.extend(4080u16.to_le_bytes());
        s.extend(remaining.to_le_bytes());
    }
    assert!(matches!(parse("ppt", &ole("PowerPoint Document", &s)), Err(ParseError::Skip(_))));
}

#[cfg(feature = "html")]
#[test]
fn html_pathological_inputs_are_linear() {
    let t = std::time::Instant::now();
    parse("html", "&".repeat(2_000_000).as_bytes()).unwrap();
    parse("html", "<style></style>".repeat(200_000).as_bytes()).unwrap();
    assert!(t.elapsed().as_secs() < 10, "quadratic behavior: {:?}", t.elapsed());
}

#[cfg(feature = "html")]
#[test]
fn html_more_entities_and_uppercase_extension() {
    let p = parse("HTML", b"<p>&copy; 2026 &mdash; caf&eacute;</p>").unwrap();
    assert_eq!(p.text, "\u{a9} 2026 \u{2014} caf\u{e9}");
    // same edge cases as bs4: semicolon-less refs, unknown names drop the `;`, bad numeric -> U+FFFD
    let q = parse("html", b"<p>a &amp text &unknown; &#x1F600; &#0; &lt</p><![CDATA[ a > b ]]><a title=it's>x</a>").unwrap();
    assert_eq!(q.text, "a & text &unknown \u{1f600} \u{fffd} <\na > b\nx");
}

#[cfg(feature = "html")]
#[test]
fn nul_bytes_are_stripped_from_parsed_text() {
    let mut late = "a".repeat(9000).into_bytes(); // past the 8 KiB binary sniff in the text parser
    late.extend(b"\0b");
    assert_eq!(parse("txt", &late).unwrap().text, format!("{}b", "a".repeat(9000)));
}

#[cfg(feature = "odf")]
#[test]
fn odf_huge_space_count_is_clamped() {
    use std::io::Write;
    let xml = r#"<office:document-content xmlns:office="o" xmlns:text="t"><office:body><office:text><text:p>a<text:s text:c="18446744073709551615"/>b</text:p></office:text></office:body></office:document-content>"#;
    let mut z = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    z.start_file("content.xml", zip::write::SimpleFileOptions::default()).unwrap();
    z.write_all(xml.as_bytes()).unwrap();
    let bytes = z.finish().unwrap().into_inner();
    let t = parse("odt", &bytes).unwrap().text;
    assert!(t.len() <= 10_002 && t.starts_with('a') && t.ends_with('b'), "{}", t.len());
}

#[cfg(feature = "doc")]
#[test]
fn ole_cycles_and_truncation_skip_cleanly() {
    let raw = std::fs::read(format!("{}/tests/fixtures/rich.doc", env!("CARGO_MANIFEST_DIR"))).unwrap();
    assert!(matches!(parse("doc", &raw[..1000]), Err(ParseError::Skip(_))));
    // make the directory chain loop on itself: fat[dir_start] = dir_start
    let u32at = |b: &[u8], o: usize| u32::from_le_bytes(b[o..o + 4].try_into().unwrap());
    let (dir0, fat0) = (u32at(&raw, 0x30) as usize, u32at(&raw, 0x4C) as usize);
    let mut cyc = raw.clone();
    let o = (fat0 + 1) * 512 + dir0 * 4;
    cyc[o..o + 4].copy_from_slice(&(dir0 as u32).to_le_bytes());
    assert!(matches!(parse("doc", &cyc), Err(ParseError::Skip(_))));
}

#[cfg(feature = "ppt")]
#[test]
fn ppt_free_text_boxes_are_read() {
    fn rec(vi: u16, ty: u16, body: &[u8]) -> Vec<u8> {
        let mut v = vi.to_le_bytes().to_vec();
        v.extend(ty.to_le_bytes());
        v.extend((body.len() as u32).to_le_bytes());
        v.extend(body);
        v
    }
    let utf16 = |s: &str| s.encode_utf16().flat_map(u16::to_le_bytes).collect::<Vec<u8>>();
    // Slide(1006) > PPDrawing(1036) > Escher ClientTextbox(0xF00D) > TextChars
    let tb = rec(0xF, 0xF00D, &rec(0, 0x0FA0, &utf16("free box")));
    let slide = rec(0xF, 1006, &rec(0xF, 1036, &tb));
    let ole = ole("PowerPoint Document", &slide);
    assert_eq!(parse("ppt", &ole).unwrap().text, "free box");
}
