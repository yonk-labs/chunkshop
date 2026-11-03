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

fn dump<R: Reader<Cursor<Vec<u8>>>>(mut wb: R, what: &str) -> Result<String, ParseError>
where
    R::Error: std::fmt::Display,
{
    let mut rows: Vec<String> = Vec::new();
    for name in wb.sheet_names() {
        let range = wb
            .worksheet_range(&name)
            .map_err(|e| ParseError::Skip(format!("failed to parse {what} sheet {name}: {e}")))?;
        let Some((r0, c0)) = range.start() else { continue };
        // openpyxl read-only pads from A1; calamine's range starts at the first used cell.
        let width = c0 as usize + range.width();
        // a lone cell at XFD1048576 would otherwise pad ~1M rows x 16k tabs
        if (r0 as usize).saturating_mul(width) > 50_000_000 {
            return Err(ParseError::Skip(format!("{what} sheet {name}: data starts too far from A1")));
        }
        rows.extend((0..r0).map(|_| "\t".repeat(width.saturating_sub(1))));
        for row in range.rows() {
            let mut cells = vec![String::new(); c0 as usize];
            cells.extend(row.iter().map(cell));
            rows.push(cells.join("\t"));
        }
    }
    Ok(rows.join("\n"))
}

fn open<W: Reader<Cursor<Vec<u8>>>>(bytes: &[u8], what: &str) -> Result<String, ParseError>
where
    W::Error: std::fmt::Display,
{
    let wb: W = open_workbook_from_rs(Cursor::new(bytes.to_vec()))
        .map_err(|e| ParseError::Skip(format!("failed to parse {what}: {e}")))?;
    dump(wb, what)
}

pub(crate) fn parse(bytes: &[u8]) -> Result<String, ParseError> {
    open::<Xlsx<_>>(bytes, "XLSX")
}
pub(crate) fn xls(bytes: &[u8]) -> Result<String, ParseError> {
    open::<Xls<_>>(bytes, "XLS")
}
pub(crate) fn xlsb(bytes: &[u8]) -> Result<String, ParseError> {
    open::<Xlsb<_>>(bytes, "XLSB")
}
pub(crate) fn ods(bytes: &[u8]) -> Result<String, ParseError> {
    open::<Ods<_>>(bytes, "ODS")
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
