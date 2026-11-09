//! DOCX / PPTX via zip + quick-xml. Mirrors python-docx `Document.paragraphs` and
//! python-pptx top-level text frames (see tests/fixtures/parsers_reference.json).
use std::io::{Cursor, Read};

use quick_xml::events::Event;
use quick_xml::Reader;
use zip::ZipArchive;

use crate::ParseError;

/// Zip-bomb guard per part. ponytail: fixed 256 MiB ceiling; make configurable if a corpus needs more.
const MAX_PART_BYTES: u64 = 256 << 20;

fn skip(what: &str, e: impl std::fmt::Display) -> ParseError {
    ParseError::Skip(format!("failed to parse {what}: {e}"))
}

type Zip<'a> = ZipArchive<Cursor<&'a [u8]>>;

fn open<'a>(bytes: &'a [u8], what: &str) -> Result<Zip<'a>, ParseError> {
    ZipArchive::new(Cursor::new(bytes)).map_err(|e| skip(what, e))
}

fn part(z: &mut Zip<'_>, name: &str, what: &str) -> Result<String, ParseError> {
    let mut buf = Vec::new();
    z.by_name(name)
        .map_err(|e| skip(what, format!("{name}: {e}")))?
        .take(MAX_PART_BYTES + 1)
        .read_to_end(&mut buf)
        .map_err(|e| skip(what, e))?;
    if buf.len() as u64 > MAX_PART_BYTES {
        return Err(skip(what, format!("{name} exceeds {MAX_PART_BYTES} bytes (zip bomb?)")));
    }
    String::from_utf8(buf).map_err(|e| skip(what, e))
}

fn name(e: &quick_xml::events::BytesStart<'_>) -> String {
    e.local_name().as_ref().to_string()
}

fn attr(e: &quick_xml::events::BytesStart<'_>, key: &str) -> Option<String> {
    e.attributes().flatten().find_map(|a| {
        (a.key.as_ref() == key)
            .then(|| a.normalized_value(quick_xml::XmlVersion::Implicit1_0).ok().map(|v| v.into_owned()))
            .flatten()
    })
}

/// Resolve `&amp;`-style refs (quick-xml >=0.38 emits them as separate events).
fn general_ref(r: &quick_xml::events::BytesRef<'_>) -> Option<String> {
    if let Ok(Some(c)) = r.resolve_char_ref() {
        return Some(c.to_string());
    }
    Some(match &**r {
        "amp" => "&",
        "lt" => "<",
        "gt" => ">",
        "quot" => "\"",
        "apos" => "'",
        _ => return None,
    }
    .to_string())
}

/// Text of `w:p` children of `w:body` only, joined by `\n` (tables, headers, footers excluded).
#[cfg(feature = "docx")]
pub(crate) fn docx(bytes: &[u8]) -> Result<String, ParseError> {
    let what = "DOCX";
    let xml = part(&mut open(bytes, what)?, "word/document.xml", what)?;
    let mut rd = Reader::from_str(&xml);
    let (mut depth, mut in_p, mut in_t, mut in_ppr) = (0usize, false, false, false);
    let (mut cur, mut paras) = (String::new(), Vec::<String>::new());
    loop {
        match rd.read_event().map_err(|e| skip(what, e))? {
            Event::Start(e) => {
                match name(&e).as_str() {
                    "p" if depth == 2 => (in_p, cur) = (true, String::new()),
                    "pPr" => in_ppr = true,
                    "t" if in_p => in_t = true,
                    _ => {}
                }
                depth += 1;
            }
            Event::Empty(e) if in_p && !in_ppr => match name(&e).as_str() {
                "tab" => cur.push('\t'),
                // ponytail: page breaks contribute nothing; python-docx maps other breaks to "\n".
                "br" if attr(&e, "w:type").as_deref() != Some("page") => cur.push('\n'),
                "cr" => cur.push('\n'),
                _ => {}
            },
            Event::End(e) => {
                depth = depth.saturating_sub(1);
                match e.local_name().as_ref() {
                    "p" if depth == 2 && in_p => {
                        in_p = false;
                        paras.push(std::mem::take(&mut cur));
                    }
                    "pPr" => in_ppr = false,
                    "t" => in_t = false,
                    _ => {}
                }
            }
            Event::Text(t) if in_t => cur.push_str(&t.xml10_content()),
            Event::GeneralRef(r) if in_t => cur.extend(general_ref(&r)),
            Event::Eof => break,
            _ => {}
        }
    }
    Ok(paras.join("\n"))
}

/// Slide order from presentation.xml (not file names); each top-level `p:sp` is one text frame
/// (paragraphs joined `\n`, soft breaks `\v`); groups, tables, pictures skipped. Frames joined `\n`.
#[cfg(feature = "pptx")]
pub(crate) fn pptx(bytes: &[u8]) -> Result<String, ParseError> {
    let what = "PPTX";
    let mut z = open(bytes, what)?;

    // relationship id -> target
    let rels = part(&mut z, "ppt/_rels/presentation.xml.rels", what)?;
    let mut targets = std::collections::HashMap::new();
    let mut rd = Reader::from_str(&rels);
    loop {
        match rd.read_event().map_err(|e| skip(what, e))? {
            Event::Start(e) | Event::Empty(e) if name(&e) == "Relationship" => {
                if let (Some(id), Some(t)) = (attr(&e, "Id"), attr(&e, "Target")) {
                    targets.insert(id, t);
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }
    let pres = part(&mut z, "ppt/presentation.xml", what)?;
    let mut order = Vec::new();
    let mut rd = Reader::from_str(&pres);
    loop {
        match rd.read_event().map_err(|e| skip(what, e))? {
            Event::Start(e) | Event::Empty(e) if name(&e) == "sldId" => {
                let t = attr(&e, "r:id")
                    .and_then(|id| targets.get(&id).cloned())
                    .ok_or_else(|| skip(what, "slide without relationship"))?;
                let path = match t.strip_prefix('/') {
                    Some(abs) => abs.to_string(),
                    None => format!("ppt/{t}"),
                };
                // a hostile deck can list one slide 100k times; python-pptx never repeats a slide part
                if !order.contains(&path) {
                    order.push(path);
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }

    let mut frames = Vec::<String>::new();
    for slide in order {
        let xml = part(&mut z, &slide, what)?;
        let mut rd = Reader::from_str(&xml);
        // depth 3 = child of p:sld > p:cSld > p:spTree
        let (mut depth, mut in_sp, mut in_t) = (0usize, false, false);
        let (mut lines, mut cur) = (Vec::<String>::new(), String::new());
        loop {
            match rd.read_event().map_err(|e| skip(what, e))? {
                Event::Start(e) => {
                    match name(&e).as_str() {
                        "sp" if depth == 3 => (in_sp, lines) = (true, Vec::new()),
                        "p" if in_sp => cur.clear(),
                        "t" if in_sp => in_t = true,
                        _ => {}
                    }
                    depth += 1;
                }
                Event::Empty(e) if in_sp && name(&e) == "br" => cur.push('\u{b}'),
                Event::End(e) => {
                    depth = depth.saturating_sub(1);
                    match e.local_name().as_ref() {
                        "sp" if depth == 3 && in_sp => {
                            in_sp = false;
                            frames.push(lines.join("\n"));
                        }
                        "p" if in_sp => lines.push(std::mem::take(&mut cur)),
                        "t" => in_t = false,
                        _ => {}
                    }
                }
                Event::Text(t) if in_t => cur.push_str(&t.xml10_content()),
                Event::GeneralRef(r) if in_t => cur.extend(general_ref(&r)),
                Event::Eof => break,
                _ => {}
            }
        }
    }
    Ok(frames.join("\n"))
}

/// ODT / ODP: every `text:p` / `text:h` in `content.xml`, in document order, joined `\n`.
/// `text:s` = spaces, `text:tab`, `text:line-break`. No Python counterpart; tests assert by hand.
#[cfg(feature = "odf")]
pub(crate) fn odf(bytes: &[u8], what: &str) -> Result<String, ParseError> {
    let xml = part(&mut open(bytes, what)?, "content.xml", what)?;
    let mut rd = Reader::from_str(&xml);
    // stack: a paragraph can hold a frame holding paragraphs
    let (mut stack, mut out) = (Vec::<String>::new(), Vec::<String>::new());
    loop {
        match rd.read_event().map_err(|e| skip(what, e))? {
            Event::Start(e) if matches!(name(&e).as_str(), "p" | "h") => stack.push(String::new()),
            Event::Empty(e) if matches!(name(&e).as_str(), "p" | "h") => out.push(String::new()),
            Event::Empty(e) if !stack.is_empty() => match name(&e).as_str() {
                "s" => {
                    let n = attr(&e, "text:c").and_then(|c| c.parse::<usize>().ok()).unwrap_or(1).min(10_000); // ponytail: clamp hostile text:c
                    stack.last_mut().unwrap().push_str(&" ".repeat(n));
                }
                "tab" => stack.last_mut().unwrap().push('\t'),
                "line-break" => stack.last_mut().unwrap().push('\n'),
                _ => {}
            },
            Event::End(e) if matches!(e.local_name().as_ref(), "p" | "h") => {
                if let Some(p) = stack.pop() {
                    out.push(p);
                }
            }
            Event::Text(t) => {
                if let Some(p) = stack.last_mut() {
                    p.push_str(&t.xml10_content());
                }
            }
            Event::GeneralRef(r) => {
                if let Some(p) = stack.last_mut() {
                    p.extend(general_ref(&r));
                }
            }
            Event::Eof => break,
            _ => {}
        }
    }
    Ok(out.join("\n"))
}
