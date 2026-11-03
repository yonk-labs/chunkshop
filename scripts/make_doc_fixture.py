#!/usr/bin/env python3
"""macOS only: write rust/chunkshop-parsers/tests/fixtures/rich.doc, a real Word-97 file made by
`textutil -convert doc` from HTML (no other .doc writer is available locally)."""
import subprocess, tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "rust/chunkshop-parsers/tests/fixtures"
HTML = ('<html><head><meta charset="utf-8"></head><body><h1>Legacy Title</h1>'
        "<p>First paragraph with “smart quotes” and café — dash.</p>"
        "<p>Second<br>line break.</p><p>Last paragraph ☃ snowman.</p></body></html>")

with tempfile.TemporaryDirectory() as t:
    (Path(t) / "in.html").write_text(HTML, encoding="utf-8")
    subprocess.run(["textutil", "-convert", "doc", "-output", str(OUT / "rich.doc"), f"{t}/in.html"], check=True)
