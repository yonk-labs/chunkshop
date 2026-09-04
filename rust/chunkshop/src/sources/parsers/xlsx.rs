//! XLSX -> text via `calamine` 0.36. Mirrors
//! `python/src/chunkshop/sources/parsers/xlsx.py`: every worksheet, every
//! row via `iter_rows(values_only=True)`, cells tab-joined, rows newline-
//! joined, `None`/empty cells become `""`. `calamine::Data` is the cell-value
//! enum, confirmed against the vendored source
//! (`~/.cargo/registry/src/*/calamine-0.36.1/src/datatype.rs`): `Int(i64)`,
//! `Float(f64)`, `String(String)`, `Bool(bool)`, `DateTime(ExcelDateTime)`,
//! `DateTimeIso(String)`, `DurationIso(String)`, `Error(CellErrorType)`,
//! `Empty` — matches what was verified via docs.rs, so no arms changed.
//! One caveat found only by reading the source: `ExcelDateTime`'s
//! `Display` impl prints the raw Excel serial number (`self.value`), not a
//! formatted date/time string — so `Data::DateTime(_).to_string()` is a
//! numeric string, not e.g. an RFC3339 date. That's a non-issue here: this
//! is a contract-level parity claim (schema, non-blank text), not a
//! value-level one, same as Python's own `str()` of `int`/`float`/`str`
//! cells not guaranteeing numeric-format parity either.

use std::path::Path;

use calamine::{open_workbook, Data, Reader, Xlsx};

use super::{Deriver, ParseResult};

pub struct XlsxDeriver;

impl Deriver for XlsxDeriver {
    /// Python's class is `XLSXParser` (initialism uppercased).
    fn parser_name(&self) -> &'static str {
        "XLSXParser"
    }

    /// Deliberately makes NO `had_content` claim (the F5 signal the PDF,
    /// DOCX and HTML derivers set). A workbook whose cells are all empty is
    /// a legitimately empty spreadsheet — a blank template — not a failed
    /// extraction, and "sheet count > 0" would fire on every one of them.
    /// The only honest signal would be non-cell content (charts, images,
    /// embedded objects), and neither calamine nor Python's openpyxl value
    /// view exposes that through a public API. Rather than invent a
    /// heuristic that produces false positives on blank templates in both
    /// ports, this deriver stays silent and an empty workbook is indexed as
    /// an empty document, exactly as before. Mirrors Python's `XLSXParser`.
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let mut wb: Xlsx<_> = open_workbook(path)
            .map_err(|e| anyhow::anyhow!("failed to parse XLSX {}: {e}", path.display()))?;
        let sheet_names = wb.sheet_names().to_vec();
        let mut rows_out: Vec<String> = Vec::new();
        for name in sheet_names {
            let range = wb
                .worksheet_range(&name)
                .map_err(|e| anyhow::anyhow!("reading sheet {name:?} in {}: {e}", path.display()))?;
            for row in range.rows() {
                let cells: Vec<String> = row.iter().map(cell_to_string).collect();
                rows_out.push(cells.join("\t"));
            }
        }
        Ok(ParseResult::new(rows_out.join("\n")))
    }
}

fn cell_to_string(cell: &Data) -> String {
    match cell {
        Data::Empty => String::new(),
        Data::String(s) => s.clone(),
        Data::Float(f) => f.to_string(),
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => b.to_string(),
        Data::Error(e) => format!("{e:?}"),
        Data::DateTime(d) => d.to_string(),
        Data::DateTimeIso(s) | Data::DurationIso(s) => s.clone(),
    }
}
