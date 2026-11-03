//! Legacy binary Office: `.doc` (Word 97-2003) and `.ppt` (PowerPoint 97-2003) via the `cfb`
//! OLE container reader plus hand-written text extraction. Local-only, no subprocesses.
//! Specs: MS-DOC (FIB, CLX/PlcPcd piece table) and MS-PPT (SlideListWithText records).
use crate::ParseError;

/// Ceiling per OLE stream and per whole container walk.
const MAX_STREAM: usize = 64 << 20;
#[cfg(feature = "ppt")]
const MAX_DEPTH: u8 = 24; // Slide > PPDrawing > Escher group nesting stays well under this

fn skip(what: &str, e: impl std::fmt::Display) -> ParseError {
    ParseError::Skip(format!("failed to parse {what}: {e}"))
}

fn rd16(b: &[u8], o: usize) -> Option<u16> {
    Some(u16::from_le_bytes(b.get(o..o.checked_add(2)?)?.try_into().ok()?))
}
fn rd32(b: &[u8], o: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(o..o.checked_add(4)?)?.try_into().ok()?))
}
/// `b[o..o+len]` without overflow on hostile offsets.
fn slice(b: &[u8], o: usize, len: usize) -> Option<&[u8]> {
    b.get(o..o.checked_add(len)?)
}

const END: u32 = 0xFFFF_FFFA; // sector ids >= this are markers (free/end/fat/difat)

/// Minimal lenient OLE/CFB reader (MS-CFB): FAT, mini-FAT, directory. Every chain walk is bounded,
/// so cycles and truncation fail cleanly. Lenient where the `cfb` crate is strict (Apple's
/// `textutil` writes duplicate MiniFAT pointers that `cfb` rejects but Word reads fine).
struct Ole<'a> {
    d: &'a [u8],
    ss: usize,
    ms: usize,
    fat: Vec<u32>,
    minifat: Vec<u32>,
    ministream: Vec<u8>,
    /// (name, start sector, size)
    dir: Vec<(String, u32, usize)>,
}

impl<'a> Ole<'a> {
    fn open(d: &'a [u8], what: &str) -> Result<Self, ParseError> {
        let bad = |m: &str| skip(what, m);
        if d.get(..8) != Some(&[0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) {
            return Err(bad("not an OLE container"));
        }
        let shift = rd16(d, 0x1E).ok_or_else(|| bad("truncated header"))?;
        let mshift = rd16(d, 0x20).ok_or_else(|| bad("truncated header"))?;
        if !(shift == 9 || shift == 12) || mshift > 12 {
            return Err(bad("unsupported sector size"));
        }
        let (ss, ms) = (1usize << shift, 1usize << mshift);
        let mut o = Ole { d, ss, ms, fat: vec![], minifat: vec![], ministream: vec![], dir: vec![] };
        let get = |i| rd32(d, i).ok_or_else(|| bad("truncated header"));
        let (nfat, dir0, mf0, ndifat, difat0) = (get(0x2C)? as usize, get(0x30)?, get(0x3C)?, get(0x48)? as usize, get(0x44)?);

        // FAT sector list: 109 header slots, then the DIFAT chain.
        let mut fat_secs: Vec<u32> = (0..109).filter_map(|i| rd32(d, 0x4C + i * 4)).filter(|&s| s < END).collect();
        let (mut next, mut steps) = (difat0, 0);
        while next < END && steps < ndifat.min(4096) {
            let sec = o.sector(next).ok_or_else(|| bad("bad DIFAT"))?;
            let n = ss / 4 - 1;
            fat_secs.extend((0..n).filter_map(|i| rd32(sec, i * 4)).filter(|&s| s < END));
            next = rd32(sec, n * 4).unwrap_or(END);
            steps += 1;
        }
        fat_secs.truncate(nfat.max(1).min(1 << 20));
        for s in fat_secs {
            let sec = o.sector(s).ok_or_else(|| bad("bad FAT sector"))?;
            o.fat.extend(sec.chunks_exact(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]])));
        }
        let dirs = o.chain(dir0, what)?;
        for e in dirs.chunks_exact(128) {
            let nl = (rd16(e, 0x40).unwrap_or(0) as usize).min(64);
            let u: Vec<u16> = e[..nl.saturating_sub(2)].chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
            o.dir.push((String::from_utf16_lossy(&u), rd32(e, 0x74).unwrap_or(END), rd32(e, 0x78).unwrap_or(0) as usize));
        }
        let (root_start, root_size) = o.dir.first().map(|e| (e.1, e.2)).ok_or_else(|| bad("empty directory"))?;
        let mf = o.chain(mf0, what).unwrap_or_default();
        o.minifat = mf.chunks_exact(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect();
        o.ministream = o.chain(root_start, what).unwrap_or_default();
        o.ministream.truncate(root_size);
        Ok(o)
    }

    fn sector(&self, n: u32) -> Option<&'a [u8]> {
        slice(self.d, (n as usize).checked_add(1)?.checked_mul(self.ss)?, self.ss)
    }

    /// Follow a FAT chain; at most `fat.len()` hops (cycle guard) and MAX_STREAM bytes.
    fn chain(&self, mut s: u32, what: &str) -> Result<Vec<u8>, ParseError> {
        let (mut out, mut hops) = (Vec::new(), 0usize);
        while s < END {
            hops += 1;
            if hops > self.fat.len() || out.len() + self.ss > MAX_STREAM {
                return Err(skip(what, "FAT chain cycle or oversized stream"));
            }
            out.extend_from_slice(self.sector(s).ok_or_else(|| skip(what, "truncated container"))?);
            s = *self.fat.get(s as usize).ok_or_else(|| skip(what, "bad FAT index"))?;
        }
        Ok(out)
    }

    fn stream(&self, name: &str, what: &str) -> Result<Vec<u8>, ParseError> {
        let (_, start, size) = self
            .dir
            .iter()
            .skip(1)
            .find(|e| e.0 == name)
            .ok_or_else(|| skip(what, format!("missing stream {name}")))?;
        if *size > MAX_STREAM {
            return Err(skip(what, format!("{name} exceeds {MAX_STREAM} bytes")));
        }
        let mut out = if *size < 4096 {
            let (mut out, mut s, mut hops) = (Vec::new(), *start, 0usize);
            while s < END {
                hops += 1;
                if hops > self.minifat.len() {
                    return Err(skip(what, "mini-FAT cycle"));
                }
                out.extend_from_slice(slice(&self.ministream, s as usize * self.ms, self.ms).ok_or_else(|| skip(what, "truncated mini stream"))?);
                s = *self.minifat.get(s as usize).ok_or_else(|| skip(what, "bad mini-FAT index"))?;
            }
            out
        } else {
            self.chain(*start, what)?
        };
        out.truncate(*size);
        Ok(out)
    }
}

/// cp1252 0x80..=0x9F (the printable subset Word actually writes); everything else is Latin-1.
fn cp1252(b: u8) -> char {
    match b {
        0x80 => '€',
        0x85 => '…',
        0x91 => '‘',
        0x92 => '’',
        0x93 => '“',
        0x94 => '”',
        0x95 => '•',
        0x96 => '–',
        0x97 => '—',
        0x99 => '™',
        b => b as char,
    }
}

#[cfg(feature = "doc")]
pub(crate) fn doc(bytes: &[u8]) -> Result<String, ParseError> {
    let what = "DOC";
    let ole = Ole::open(bytes, what)?;
    let wd = ole.stream("WordDocument", what)?;
    let bad = || skip(what, "truncated or unsupported FIB");
    if rd16(&wd, 0).ok_or_else(bad)? != 0xA5EC {
        return Err(skip(what, "not a Word 97+ document"));
    }
    let flags = rd16(&wd, 0x0A).ok_or_else(bad)?;
    if flags & 0x0100 != 0 {
        return Err(skip(what, "encrypted"));
    }
    let table = ole.stream(if flags & 0x0200 != 0 { "1Table" } else { "0Table" }, what)?;
    let ccp_text = (rd32(&wd, 0x4C).ok_or_else(bad)? as usize).min(wd.len()); // main body only; a piece can never yield more chars than the stream has bytes
    let (fc_clx, lcb_clx) = (rd32(&wd, 0x1A2).ok_or_else(bad)? as usize, rd32(&wd, 0x1A6).ok_or_else(bad)? as usize);
    let clx = slice(&table, fc_clx, lcb_clx).ok_or_else(bad)?;

    // CLX = Prc* then Pcdt (0x02, lcb, PlcPcd)
    let mut p = 0;
    while clx.get(p) == Some(&1) {
        p += 3 + rd16(clx, p + 1).ok_or_else(bad)? as usize;
    }
    if clx.get(p) != Some(&2) {
        return Err(bad());
    }
    let lcb = rd32(clx, p + 1).ok_or_else(bad)? as usize;
    let plc = slice(clx, p + 5, lcb).ok_or_else(bad)?;
    let n = (lcb.checked_sub(4).ok_or_else(bad)?) / 12; // (n+1)*4 CPs + n*8 Pcd
    let (mut raw, mut left) = (String::new(), ccp_text);
    for i in 0..n {
        let cp0 = rd32(plc, i * 4).ok_or_else(bad)? as usize;
        let cp1 = rd32(plc, (i + 1) * 4).ok_or_else(bad)? as usize;
        let fc = rd32(plc, (n + 1) * 4 + i * 8 + 2).ok_or_else(bad)?;
        let len = cp1.saturating_sub(cp0).min(left);
        if fc & 0x4000_0000 != 0 {
            let o = ((fc & 0x3FFF_FFFF) / 2) as usize;
            raw.extend(slice(&wd, o, len).ok_or_else(bad)?.iter().map(|&b| cp1252(b)));
        } else {
            let o = fc as usize;
            let u: Vec<u16> = slice(&wd, o, len.checked_mul(2).ok_or_else(bad)?).ok_or_else(bad)?.chunks(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
            raw.push_str(&String::from_utf16_lossy(&u));
        }
        left -= len;
    }

    // Control characters -> text. Field instructions (0x13..0x14) are dropped, results kept.
    let (mut out, mut fields, mut in_code) = (String::new(), Vec::<bool>::new(), 0usize); // true = in result; in_code = fields still in instruction mode
    for c in raw.chars() {
        match c {
            '\x13' => {
                fields.push(false);
                in_code += 1;
            }
            '\x14' => {
                if let Some(f) = fields.last_mut().filter(|f| !**f) {
                    *f = true;
                    in_code -= 1;
                }
            }
            '\x15' => {
                if fields.pop() == Some(false) {
                    in_code -= 1;
                }
            }
            _ if in_code > 0 => {}
            '\r' | '\x0b' => {
                // an unmatched field start must not swallow the rest of the document
                (fields.clear(), in_code = 0);
                out.push('\n');
            }
            // cell mark = tab; the row-end mark is a second \x07 right after it = newline
            '\x07' if out.ends_with('\t') => {
                out.pop();
                out.push('\n');
            }
            '\x07' => out.push('\t'),
            '\x0c' | '\x01' | '\x08' => {}
            c => out.push(c),
        }
    }
    // ponytail: tables flatten to tab-separated rows; docx excludes tables, doc does not.
    Ok(out.strip_suffix('\n').unwrap_or(&out).to_string())
}

#[cfg(feature = "ppt")]
pub(crate) fn ppt(bytes: &[u8]) -> Result<String, ParseError> {
    let what = "PPT";
    let s = Ole::open(bytes, what)?.stream("PowerPoint Document", what)?;

    #[derive(Clone, Copy, PartialEq)]
    enum Mode {
        Top,
        Outline, // inside SlideListWithText (slide placeholders' text)
        Slide,   // inside a Slide container (free text boxes via Escher ClientTextbox)
    }
    fn text_of(ty: u16, body: &[u8]) -> String {
        if ty == 0x0FA0 {
            let u: Vec<u16> = body.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
            String::from_utf16_lossy(&u)
        } else {
            body.iter().map(|&b| cp1252(b)).collect()
        }
    }
    // Record header: ver/instance u16, type u16, len u32; container when ver == 0xF.
    fn walk(s: &[u8], depth: u8, mode: Mode, outline: &mut Vec<String>, free: &mut Vec<String>) {
        let mut p = 0;
        while let (Some(vi), Some(ty), Some(len)) = (rd16(s, p), rd16(s, p + 2), rd32(s, p + 4)) {
            let Some(body) = slice(s, p + 8, len as usize) else { return };
            let container = vi & 0xF == 0xF && depth < MAX_DEPTH;
            match (mode, ty) {
                // Document(1000) > SlideListWithText(4080, instance 0 = slides; masters/notes skipped)
                (Mode::Top, 1000) if container => walk(body, depth + 1, Mode::Top, outline, free),
                (Mode::Top, 4080) if container && vi >> 4 == 0 => walk(body, depth + 1, Mode::Outline, outline, free),
                (Mode::Top, 1006) if container => walk(body, depth + 1, Mode::Slide, outline, free),
                (Mode::Outline, 0x0F9F) => outline.push(String::new()), // TextHeaderAtom starts a block
                (Mode::Outline, 0x0FA0 | 0x0FA8) => {
                    if outline.is_empty() {
                        outline.push(String::new());
                    }
                    outline.last_mut().unwrap().push_str(&text_of(ty, body).replace(['\r', '\x0b'], "\n"));
                }
                (Mode::Slide, 0x0FA0 | 0x0FA8) => free.push(text_of(ty, body).replace(['\r', '\x0b'], "\n")),
                (Mode::Slide, _) if container => walk(body, depth + 1, Mode::Slide, outline, free),
                _ => {}
            }
            p = match p.checked_add(8).and_then(|x| x.checked_add(len as usize)) {
                Some(n) => n,
                None => return,
            };
        }
    }
    let (mut outline, mut free) = (Vec::new(), Vec::new());
    walk(&s, 0, Mode::Top, &mut outline, &mut free);
    // ponytail: free text boxes are appended after the outline text (not interleaved per slide);
    // exact slide association needs SlidePersistAtom id matching. Add if a corpus needs it.
    let blocks: Vec<String> = outline.into_iter().chain(free).filter(|b| !b.trim().is_empty()).collect();
    if blocks.is_empty() {
        return Err(skip(what, "no slide text (encrypted or image-only)"));
    }
    Ok(blocks.join("\n"))
}
