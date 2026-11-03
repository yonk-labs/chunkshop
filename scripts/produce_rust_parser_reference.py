#!/usr/bin/env python3
"""Generate rich parser fixtures + the Python reference output for chunkshop-parsers (Rust add-on).

Run from python/ with the all-parsers extra synced:
    uv run --no-sync python ../scripts/produce_rust_parser_reference.py

Writes rust/chunkshop-parsers/tests/fixtures/* and parsers_reference.json
({file: {parser, text | error, input_sha256}, versions}). References change only when
a Python parser lib version bumps (see the versions block), never to make Rust pass.
"""
from __future__ import annotations

import hashlib
import json
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "rust" / "chunkshop-parsers" / "tests" / "fixtures"


def make_xlsx() -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "First"
    ws.append(["name", "count", "ratio", "ok"])
    ws.append(["alpha", 3, 1.5, True])
    ws.append(["beta", None, 2.25, False])
    ws.append([None, None, None, "tail"])
    ws2 = wb.create_sheet("Second")
    ws2.append(["only", "sheet two"])
    wb.save(OUT / "rich.xlsx")


def make_docx() -> None:
    import docx

    d = docx.Document()
    d.add_heading("Title line", level=1)
    p = d.add_paragraph("before tab")
    p.add_run().add_tab()
    p.add_run("after tab")
    p.add_run().add_break()
    p.add_run("after break")
    d.add_paragraph("plain paragraph")
    t = d.add_table(rows=1, cols=2)  # table text must be excluded (python-docx d.paragraphs)
    t.cell(0, 0).text = "TABLE-CELL-A"
    t.cell(0, 1).text = "TABLE-CELL-B"
    d.add_paragraph("last paragraph")
    d.sections[0].header.paragraphs[0].text = "HEADER-TEXT"
    d.sections[0].footer.paragraphs[0].text = "FOOTER-TEXT"
    d.save(OUT / "rich.docx")


def make_pptx() -> None:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    for label in ("A", "B", "C"):
        s = prs.slides.add_slide(prs.slide_layouts[5])  # title only
        s.shapes.title.text = f"Slide {label}"
        tb = s.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
        tf = tb.text_frame
        tf.text = f"body {label} line1"
        tf.add_paragraph().text = f"body {label} line2"
        if label == "B":
            tf.paragraphs[0].add_line_break()  # soft break -> \v
            tf.paragraphs[0].add_run().text = "after soft break"
            tbl = s.shapes.add_table(1, 1, Inches(1), Inches(3), Inches(2), Inches(1))
            tbl.table.cell(0, 0).text = "TABLE-SKIPPED"
            g = s.shapes.add_group_shape()
            g.shapes.add_textbox(Inches(1), Inches(4), Inches(2), Inches(1)).text_frame.text = "GROUP-SKIPPED"
    # presentation order != slideN.xml file order: move last slide (C) to the front
    lst = prs.slides._sldIdLst
    last = list(lst)[-1]
    lst.remove(last)
    lst.insert(0, last)
    prs.save(OUT / "rich.pptx")


def make_pdfs() -> None:
    def pdf(pages: list[str]) -> bytes:
        objs: list[bytes] = []
        n = len(pages)
        kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
        objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
        for i, text in enumerate(pages):
            content = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode()
            objs.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode()
            )
            objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        out = b"%PDF-1.4\n"
        offs = []
        for i, o in enumerate(objs, 1):
            offs.append(len(out))
            out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
        for o in offs:
            out += f"{o:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return out

    from pypdf import PdfReader, PdfWriter

    raw = pdf(["Page one text", "Page two text"])
    (OUT / "rich.pdf").write_bytes(raw)
    w = PdfWriter(clone_from=PdfReader(OUT / "rich.pdf"))
    w.encrypt("secret")
    with open(OUT / "encrypted.pdf", "wb") as f:
        w.write(f)
    w = PdfWriter()
    w.add_blank_page(200, 200)
    with open(OUT / "blank.pdf", "wb") as f:
        w.write(f)
    (OUT / "truncated.pdf").write_bytes(raw[:120])


HTML = """<!DOCTYPE html><html><head><title>T &amp; Co</title><style>p{color:red}</style>
<script>var x = "<b>no</b>";</script></head><body><!-- hidden --><h1>Head&nbsp;line</h1>
<p>one <b>bold</b> &lt;two&gt; &#65;&#x42; a < b</p><a href="x>y">link</a><br/>
<div>  spaced   text  </div></body></html>"""


def make_html() -> None:
    (OUT / "rich.html").write_text(HTML, encoding="utf-8")


def make_odf() -> None:
    """Hand-built ODF zips (no ODF writer installed; there is no Python reference parser for these)."""
    import zipfile

    ns = (
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
    )
    body_t = (
        '<text:h>Title</text:h><text:p>a<text:s text:c="3"/>b<text:tab/>c<text:line-break/>d &amp; e</text:p>'
        "<text:p/><text:p><text:span>span</text:span> end</text:p>"
    )
    odt = f'<office:document-content {ns}><office:body><office:text>{body_t}</office:text></office:body></office:document-content>'
    odp = (
        f'<office:document-content {ns}><office:body><office:presentation>'
        '<draw:page><draw:frame><draw:text-box><text:p>slide one</text:p></draw:text-box></draw:frame></draw:page>'
        '<draw:page><draw:frame><draw:text-box><text:p>slide two</text:p></draw:text-box></draw:frame></draw:page>'
        "</office:presentation></office:body></office:document-content>"
    )
    ods = (
        f'<office:document-content {ns} xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0">'
        '<office:body><office:spreadsheet><table:table table:name="S1">'
        '<table:table-row><table:table-cell office:value-type="string"><text:p>a</text:p></table:table-cell>'
        '<table:table-cell office:value-type="string"><text:p>b</text:p></table:table-cell></table:table-row>'
        '<table:table-row><table:table-cell office:value-type="float" office:value="1"><text:p>1</text:p></table:table-cell>'
        '<table:table-cell office:value-type="float" office:value="2.5"><text:p>2.5</text:p></table:table-cell></table:table-row>'
        "</table:table></office:spreadsheet></office:body></office:document-content>"
    )
    for ext, mime, xml in (
        ("odt", "text", odt),
        ("odp", "presentation", odp),
        ("ods", "spreadsheet", ods),
    ):
        with zipfile.ZipFile(OUT / f"rich.{ext}", "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", f"application/vnd.oasis.opendocument.{mime}", compress_type=zipfile.ZIP_STORED)
            z.writestr("content.xml", xml)
            z.writestr(
                "META-INF/manifest.xml",
                '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"/>',
            )
    (OUT / "truncated.odt").write_bytes((OUT / "rich.odt").read_bytes()[:100])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    make_xlsx()
    make_docx()
    make_pptx()
    make_pdfs()
    make_html()
    make_odf()
    raw = (OUT / "rich.docx").read_bytes()
    (OUT / "truncated.docx").write_bytes(raw[:200])

    sys.path.insert(0, str(ROOT / "python" / "src"))
    from chunkshop.sources.parsers import docx as _d, pdf as _p, pptx as _t, xlsx as _x  # noqa: F401
    from chunkshop.sources.parsers.docx import DOCXParser
    from chunkshop.sources.parsers.html import HTMLParser
    from chunkshop.sources.parsers.pdf import PDFParser
    from chunkshop.sources.parsers.pptx import PPTXParser
    from chunkshop.sources.parsers.xlsx import XLSXParser

    by_ext = {"pdf": PDFParser(), "docx": DOCXParser(), "pptx": PPTXParser(), "xlsx": XLSXParser(), "html": HTMLParser()}
    ref: dict = {}
    for f in sorted(OUT.iterdir()):
        ext = f.suffix.lstrip(".")
        if ext not in by_ext:
            continue
        entry: dict = {
            "parser": type(by_ext[ext]).__name__,
            "input_sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
        }
        try:
            entry["text"] = by_ext[ext].parse(f)
        except Exception as exc:  # recorded, not fatal: Rust maps these to Skip
            entry["error"] = type(exc).__name__
        ref[f.name] = entry
    ref["_versions"] = {k: version(k) for k in ("pypdf", "python-docx", "python-pptx", "openpyxl", "beautifulsoup4")}
    (OUT / "parsers_reference.json").write_text(json.dumps(ref, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(ref) - 1} fixtures + reference to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
