"""Demonstrate `FilesSource` rich-document dispatch on a mixed-format corpus.

Walks one or more globs, parses each file with the extension-appropriate
parser, and prints a one-line summary per document showing which parser was
used and how many characters of text it produced.

Usage:

    # Default — point at the test fixtures shipped with chunkshop.
    uv run python examples/parse_corpus.py

    # Custom corpus.
    uv run python examples/parse_corpus.py '/path/to/docs/**/*'

Requires the optional parser extras you actually need:

    pip install 'chunkshop[all-parsers]'

Plain-text formats (.txt, .md, .csv, ...) work with no extras.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the example runnable from a checkout without installing chunkshop.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chunkshop.config import FilesSource as Cfg  # noqa: E402
from chunkshop.sources.files import FilesSource  # noqa: E402


def _default_glob() -> str:
    """Point at the per-format fixtures bundled with the test suite."""
    fixtures = ROOT / "tests" / "fixtures" / "parsers"
    return str(fixtures / "*")


def main(argv: list[str]) -> int:
    globs = argv[1:] or [_default_glob()]
    total = 0
    for g in globs:
        print(f"# glob: {g}")
        try:
            src = FilesSource(Cfg(type="files", glob=g))
            for doc in src.iter_documents():
                parser = doc.metadata.get("parser", "?")
                size = len(doc.content)
                preview = doc.content[:60].replace("\n", " ")
                print(f"  - {doc.title!s:40s} parser={parser:12s} chars={size:>6d} | {preview!r}")
                total += 1
        except ValueError as exc:
            print(f"  (no files matched: {exc})")
    print(f"# {total} document(s) parsed across {len(globs)} glob(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
