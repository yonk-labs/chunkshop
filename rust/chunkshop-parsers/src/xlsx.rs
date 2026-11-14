//! XLSX via calamine. Mirrors Python XLSXParser: all sheets in workbook order, cells joined
//! by `\t`, rows by `\n`, Python `str()` formatting.
use std::io::Cursor;

use calamine::{open_workbook_from_rs, Data, Ods, Reader, Xls, Xlsb, Xlsx};

use crate::ParseError;

/// Python `str(float)`: shortest round-trip digits, exponent form outside 1e-4..1e16, `nan`/`inf`.
fn py_float(f: f64) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return if f < 0.0 { "-inf" } else { "inf" }.into();
    }
    let sci = format!("{f:e}"); // shortest digits, e.g. "1.5e-7"
    let (mant, exp) = sci.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    if (-4..16).contains(&exp) {
        let s = f.to_string(); // Rust Display never uses an exponent
        if s.contains('.') { s } else { format!("{s}.0") }
    } else {
        format!("{mant}e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs())
    }
}

fn cell(c: &Data) -> String {
    match c {
        Data::Empty => String::new(),
        Data::String(s) => s.clone(),
        // xlsx stores ints and whole floats identically; Python (openpyxl) reads them as int.
        Data::Float(f) if f.fract() == 0.0 && f.abs() < 1e15 => format!("{}", *f as i64),
        Data::Float(f) => py_float(*f),
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => if *b { "True" } else { "False" }.into(),
        // ponytail: dates render as the Excel serial, not Python's "2024-01-01 00:00:00"
        // (calamine `dates` feature + chrono would fix it; add when a corpus needs it).
        other => other.to_string(),
    }
}

/// Largest dense range (rows x cols, counted from the first used cell) we will materialize.
const MAX_CELLS: u64 = 50_000_000;

/// Bounding box of the non-empty cells, accumulated while streaming, so the dense allocation that
/// `worksheet_range` makes (`Range::from_sparse`, ~32 bytes per cell of the box) can be refused first.
#[derive(Default)]
struct Extent(Option<(u32, u32, u32, u32)>);

impl Extent {
    fn add(&mut self, (r, c): (u32, u32)) {
        let (r0, r1, c0, c1) = self.0.unwrap_or((r, r, c, c));
        self.0 = Some((r0.min(r), r1.max(r), c0.min(c), c1.max(c)));
    }
    fn check(&self, what: &str, name: &str) -> Result<(), ParseError> {
        let Some((r0, r1, c0, c1)) = self.0 else { return Ok(()) };
        let cells = (u64::from(r1 - r0) + 1) * (u64::from(c1 - c0) + 1);
        if cells > MAX_CELLS {
            return Err(ParseError::Skip(format!("{what} sheet {name}: used range is too large ({cells} cells, limit {MAX_CELLS})")));
        }
        Ok(())
    }
}

/// Streams the sheet's cells (no dense range) to measure its extent. XLSX/XLSB only: calamine's
/// cell readers exist for those two; XLS and ODS have no pre-materialization hook.
fn pre_xlsx(wb: &mut Xlsx<Cursor<Vec<u8>>>, name: &str, what: &str) -> Result<(), ParseError> {
    let skip = |e: &dyn std::fmt::Display| ParseError::Skip(format!("failed to parse {what} sheet {name}: {e}"));
    let mut rd = match wb.worksheet_cells_reader(name) {
        Ok(r) => r,
        Err(calamine::XlsxError::NotAWorksheet(_)) => return Ok(()), // chartsheet: worksheet_range returns empty
        Err(e) => return Err(skip(&e)),
    };
    let mut ext = Extent::default();
    while let Some(c) = rd.next_cell().map_err(|e| skip(&e))? {
        if !matches!(c.get_value(), calamine::DataRef::Empty) {
            ext.add(c.get_position());
        }
    }
    ext.check(what, name)
}

fn pre_xlsb(wb: &mut Xlsb<Cursor<Vec<u8>>>, name: &str, what: &str) -> Result<(), ParseError> {
    let skip = |e: &dyn std::fmt::Display| ParseError::Skip(format!("failed to parse {what} sheet {name}: {e}"));
    let mut rd = wb.worksheet_cells_reader(name).map_err(|e| skip(&e))?;
    let mut ext = Extent::default();
    while let Some(c) = rd.next_cell().map_err(|e| skip(&e))? {
        if !matches!(c.get_value(), calamine::DataRef::Empty) {
            ext.add(c.get_position());
        }
    }
    ext.check(what, name)
}

fn no_pre<R>(_: &mut R, _: &str, _: &str) -> Result<(), ParseError> {
    Ok(())
}

fn dump<R: Reader<Cursor<Vec<u8>>>>(
    mut wb: R,
    what: &str,
    pre: fn(&mut R, &str, &str) -> Result<(), ParseError>,
) -> Result<String, ParseError>
where
    R::Error: std::fmt::Display,
{
    let mut rows: Vec<String> = Vec::new();
    let mut total = 0usize;
    for name in wb.sheet_names() {
        pre(&mut wb, &name, what)?;
        let range = wb
            .worksheet_range(&name)
            .map_err(|e| ParseError::Skip(format!("failed to parse {what} sheet {name}: {e}")))?;
        let Some((r0, c0)) = range.start() else { continue };
        // openpyxl read-only pads from A1; calamine's range starts at the first used cell.
        let width = c0 as usize + range.width();
        // a lone cell at XFD1048576 would otherwise pad ~1M rows x 16k tabs
        if (r0 as usize).saturating_mul(width) > MAX_CELLS as usize {
            return Err(ParseError::Skip(format!("{what} sheet {name}: data starts too far from A1")));
        }
        // padding rows are tabs only; count them before allocating
        total = total.saturating_add((r0 as usize).saturating_mul(width.max(1)));
        if total > crate::MAX_TEXT_BYTES {
            return Err(ParseError::Skip(format!("failed to parse {what}: extracted text exceeds {} bytes", crate::MAX_TEXT_BYTES)));
        }
        rows.extend((0..r0).map(|_| "\t".repeat(width.saturating_sub(1))));
        for row in range.rows() {
            let mut cells = vec![String::new(); c0 as usize];
            cells.extend(row.iter().map(cell));
            let line = cells.join("\t");
            total += line.len() + 1;
            if total > crate::MAX_TEXT_BYTES {
                return Err(ParseError::Skip(format!("failed to parse {what}: extracted text exceeds {} bytes", crate::MAX_TEXT_BYTES)));
            }
            rows.push(line);
        }
    }
    Ok(rows.join("\n"))
}

fn open<W: Reader<Cursor<Vec<u8>>>>(
    bytes: &[u8],
    what: &str,
    pre: fn(&mut W, &str, &str) -> Result<(), ParseError>,
) -> Result<String, ParseError>
where
    W::Error: std::fmt::Display,
{
    let wb: W = open_workbook_from_rs(Cursor::new(bytes.to_vec()))
        .map_err(|e| ParseError::Skip(format!("failed to parse {what}: {e}")))?;
    dump(wb, what, pre)
}

pub(crate) fn parse(bytes: &[u8]) -> Result<String, ParseError> {
    open::<Xlsx<_>>(bytes, "XLSX", pre_xlsx)
}
// ponytail: XLS is bounded by BIFF8 itself (65,536 x 256 cells); ODS by calamine's own 100M-cell cap.
// Neither exposes a streaming reader, so only the post-materialization guards apply to them.
pub(crate) fn xls(bytes: &[u8]) -> Result<String, ParseError> {
    open::<Xls<_>>(bytes, "XLS", no_pre)
}
pub(crate) fn xlsb(bytes: &[u8]) -> Result<String, ParseError> {
    open::<Xlsb<_>>(bytes, "XLSB", pre_xlsb)
}
pub(crate) fn ods(bytes: &[u8]) -> Result<String, ParseError> {
    open::<Ods<_>>(bytes, "ODS", no_pre)
}

#[cfg(test)]
mod tests {
    #[test]
    fn py_float_matches_python_str() {
        for (f, want) in [(0.1, "0.1"), (1.5, "1.5"), (1e-7, "1e-07"), (1.5e16, "1.5e+16"), (1e16, "1e+16"), (0.0001, "0.0001"), (0.00001, "1e-05"), (123456789.125, "123456789.125"), (-2.5e-10, "-2.5e-10"), (f64::NAN, "nan"), (f64::INFINITY, "inf"), (1e15 + 0.5, "1000000000000000.5")] {
            assert_eq!(super::py_float(f), want, "{f}");
        }
    }
}
