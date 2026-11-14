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

// ---- resource-exhaustion regressions (PR-020: RSC-01..04). A Rust allocation failure aborts the
// process, so these inputs must be refused BEFORE the big allocation. Run unfixed code only under
// `ulimit -v`. ----

#[cfg(any(feature = "xlsx", feature = "odf"))]
fn zip_of(parts: &[(&str, &[u8])]) -> Vec<u8> {
    use std::io::Write;
    let mut z = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    for (name, body) in parts {
        z.start_file(*name, zip::write::SimpleFileOptions::default()).unwrap();
        z.write_all(body).unwrap();
    }
    z.finish().unwrap().into_inner()
}

#[cfg(feature = "xlsx")]
fn xlsx_with_sheet(sheet_data: &str) -> Vec<u8> {
    let ct = r#"<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>"#;
    let rels = r#"<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"#;
    let wb = r#"<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>"#;
    let wbr = r#"<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>"#;
    let sh = format!(r#"<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet_data}</sheetData></worksheet>"#);
    zip_of(&[
        ("[Content_Types].xml", ct.as_bytes()),
        ("_rels/.rels", rels.as_bytes()),
        ("xl/workbook.xml", wb.as_bytes()),
        ("xl/_rels/workbook.xml.rels", wbr.as_bytes()),
        ("xl/worksheets/sheet1.xml", sh.as_bytes()),
    ])
}

#[cfg(feature = "xlsx")]
#[test]
fn xlsx_sparse_far_cell_is_skipped_before_dense_allocation() {
    // 1.4 KB file: A1 plus AAA1000000 -> a dense 1,000,000 x 703 range (~22 GB) without the guard.
    let b = xlsx_with_sheet(r#"<row r="1"><c r="A1"><v>1</v></c></row><row r="1000000"><c r="AAA1000000"><v>2</v></c></row>"#);
    assert!(b.len() < 2000);
    let t = std::time::Instant::now();
    assert!(matches!(parse("xlsx", &b), Err(ParseError::Skip(m)) if m.contains("too large")));
    assert!(t.elapsed().as_secs() < 5);
}

#[cfg(feature = "xlsx")]
#[test]
fn xlsx_small_sheets_still_parse_the_same() {
    let b = xlsx_with_sheet(r#"<row r="1"><c r="A1"><v>1</v></c><c r="B1"><v>2.5</v></c></row><row r="2"><c r="B2" t="inlineStr"><is><t>x</t></is></c></row>"#);
    assert_eq!(parse("xlsx", &b).unwrap().text, "1\t2.5\n\tx");
    // data offset from A1 keeps the existing leading-pad behavior
    let b = xlsx_with_sheet(r#"<row r="2"><c r="B2" t="inlineStr"><is><t>x</t></is></c></row>"#);
    assert_eq!(parse("xlsx", &b).unwrap().text, "\t\n\tx");
}

#[cfg(feature = "odf")]
#[test]
fn odt_space_run_amplification_is_capped() {
    // ~0.5 MB of XML, 20,000 clamped runs of 10,000 spaces = 200 MB of output without a total cap.
    let xml = format!(
        r#"<office:document-content xmlns:office="o" xmlns:text="t"><office:body><office:text><text:p>{}</text:p></office:text></office:body></office:document-content>"#,
        r#"<text:s text:c="10000"/>"#.repeat(20_000)
    );
    let b = zip_of(&[("content.xml", xml.as_bytes())]);
    assert!(matches!(parse("odt", &b), Err(ParseError::Skip(m)) if m.contains("extracted text exceeds")));
}

#[cfg(feature = "odf")]
#[test]
fn odt_small_document_still_parses_the_same() {
    let xml = r#"<office:document-content xmlns:office="o" xmlns:text="t"><office:body><office:text><text:p>a<text:s text:c="3"/>b</text:p><text:h>H</text:h></office:text></office:body></office:document-content>"#;
    let b = zip_of(&[("content.xml", xml.as_bytes())]);
    assert_eq!(parse("odt", &b).unwrap().text, "a   b\nH");
}

#[cfg(feature = "pptx")]
#[test]
fn pptx_many_compressible_parts_hit_the_aggregate_cap() {
    use std::io::Write;
    // 3 slides x 90 MiB of XML (an XML comment: compresses ~1000:1, tiny text) = 270 MiB > 256 MiB
    // aggregate, though each part is under the per-part cap.
    let rels: String = (1..=3).map(|i| format!(r#"<Relationship Id="r{i}" Target="slides/s{i}.xml"/>"#)).collect();
    let rels = format!("<Relationships>{rels}</Relationships>");
    let ids: String = (1..=3).map(|i| format!(r#"<p:sldId r:id="r{i}"/>"#)).collect();
    let pres = format!("<p:presentation><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>");
    let mut z = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    let opt = zip::write::SimpleFileOptions::default();
    z.start_file("ppt/_rels/presentation.xml.rels", opt).unwrap();
    z.write_all(rels.as_bytes()).unwrap();
    z.start_file("ppt/presentation.xml", opt).unwrap();
    z.write_all(pres.as_bytes()).unwrap();
    for i in 1..=3 {
        z.start_file(format!("ppt/slides/s{i}.xml"), opt).unwrap();
        z.write_all(b"<p:sld><p:cSld><p:spTree><p:sp><a:p><a:t>hi</a:t></a:p></p:sp><!--").unwrap();
        let chunk = vec![b'a'; 1 << 20];
        for _ in 0..90 {
            z.write_all(&chunk).unwrap();
        }
        z.write_all(b"--></p:spTree></p:cSld></p:sld>").unwrap();
    }
    let b = z.finish().unwrap().into_inner();
    assert!(b.len() < 1 << 20, "{}", b.len());
    assert!(matches!(parse("pptx", &b), Err(ParseError::Skip(m)) if m.contains("total")));
}

#[cfg(feature = "doc")]
#[test]
fn doc_ministream_chain_is_bounded_by_declared_size() {
    // 1 MB file; WordDocument declares 100 bytes but its mini-FAT chain is a self-loop with
    // 262,144 entries of 4096 bytes each: ~1 GiB read without a declared-size bound.
    use std::io::Write;
    let (nf, nm, r) = (18usize, 2048usize, 8usize);
    let (dirs, root) = (nf, nf + 1);
    let mf0 = root + r;
    let mut h = vec![0u8; 512];
    h[..8].copy_from_slice(&[0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]);
    let w16 = |h: &mut Vec<u8>, o: usize, v: u16| h[o..o + 2].copy_from_slice(&v.to_le_bytes());
    let w32 = |h: &mut Vec<u8>, o: usize, v: u32| h[o..o + 4].copy_from_slice(&v.to_le_bytes());
    for (o, v) in [(0x18, 0x3e), (0x1A, 3), (0x1C, 0xFFFE), (0x1E, 9), (0x20, 12)] {
        w16(&mut h, o, v);
    }
    for (o, v) in [(0x2C, nf as u32), (0x30, dirs as u32), (0x3C, mf0 as u32), (0x44, 0xFFFF_FFFE), (0x48, 0)] {
        w32(&mut h, o, v);
    }
    for i in 0..109 {
        w32(&mut h, 0x4C + i * 4, if i < nf { i as u32 } else { 0xFFFF_FFFF });
    }
    let mut fat = vec![0xFFFF_FFFFu32; nf * 128];
    fat[..nf].fill(0xFFFF_FFFD);
    fat[dirs] = 0xFFFF_FFFE;
    for k in 0..r {
        fat[root + k] = if k < r - 1 { (root + k + 1) as u32 } else { 0xFFFF_FFFE };
    }
    for k in 0..nm {
        fat[mf0 + k] = if k < nm - 1 { (mf0 + k + 1) as u32 } else { 0xFFFF_FFFE };
    }
    let ent = |name: &str, start: u32, size: u32| {
        let mut e = vec![0u8; 128];
        let u: Vec<u8> = format!("{name}\0").encode_utf16().flat_map(u16::to_le_bytes).collect();
        e[..u.len()].copy_from_slice(&u);
        e[0x40..0x42].copy_from_slice(&(u.len() as u16).to_le_bytes());
        e[0x74..0x78].copy_from_slice(&start.to_le_bytes());
        e[0x78..0x7C].copy_from_slice(&size.to_le_bytes());
        e
    };
    let mut out = h;
    out.extend(fat.iter().flat_map(|v| v.to_le_bytes()));
    out.extend(ent("Root Entry", root as u32, 16384));
    out.extend(ent("WordDocument", 0, 100));
    out.extend(vec![0u8; 256]);
    out.extend(vec![b'B'; 512 * r]);
    out.extend(vec![0u8; 512 * nm]);
    let t = std::time::Instant::now();
    assert!(matches!(parse("doc", &out), Err(ParseError::Skip(_))));
    assert!(t.elapsed().as_secs() < 5);
}
