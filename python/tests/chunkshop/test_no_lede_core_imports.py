"""SC-016: only the three designated shim files may import lede / lede_spacy."""
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "chunkshop"

ALLOWED = {
    SRC / "summarizers" / "lede.py",
    SRC / "extractors" / "lede_top_terms.py",
    SRC / "hints.py",
}

IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+lede(?:_spacy)?\b", re.MULTILINE)


def test_no_lede_imports_in_core():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        if IMPORT_RE.search(text):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"lede/lede_spacy imported outside shim files: {offenders}"
