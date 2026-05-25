"""Run the ``code_aware`` chunker over chunkshop's own source tree.

Walks a directory of ``.py`` files and prints one line per emitted chunk
showing the file, node type, node name, line range, and character count of
both ``original_content`` (raw source) and ``embedded_content`` (raw source
plus an optional import-block prefix).

Usage::

    # Default — chunk chunkshop's own chunkers/ package.
    uv run python examples/chunk_python_code.py

    # Custom directory of Python files.
    uv run python examples/chunk_python_code.py /path/to/some/python/package

This is a zero-dep demo: ``code_aware`` only needs the stdlib for the Python
path. No embedder is loaded.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the example runnable from a checkout without installing chunkshop.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chunkshop.chunkers import load_chunker  # noqa: E402
from chunkshop.config import CodeAwareChunker  # noqa: E402
from chunkshop.sources.base import Document  # noqa: E402


def _default_root() -> Path:
    """Point at chunkshop's own chunkers/ package — a small, well-formed example."""
    return ROOT / "src" / "chunkshop" / "chunkers"


def _iter_python_files(root: Path):
    if root.is_file() and root.suffix == ".py":
        yield root
        return
    for path in sorted(root.rglob("*.py")):
        # Skip __pycache__ and any compiled artifacts.
        if "__pycache__" in path.parts:
            continue
        yield path


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else _default_root()
    if not target.exists():
        print(f"error: {target} does not exist")
        return 1

    chunker = load_chunker(
        CodeAwareChunker(type="code_aware", include_imports=True, max_chars=4000)
    )

    total_chunks = 0
    total_files = 0
    for py in _iter_python_files(target):
        try:
            content = py.read_text()
        except OSError as exc:  # pragma: no cover - filesystem oddities
            print(f"  skip {py}: {exc}")
            continue
        doc = Document(
            id=str(py),
            content=content,
            metadata={"path": str(py)},
        )
        chunks = chunker.chunk(doc)
        total_files += 1
        rel = py.relative_to(target.parent) if py.is_relative_to(target.parent) else py
        print(f"# {rel} -> {len(chunks)} chunk(s)")
        for c in chunks:
            node_type = c.metadata.get("node_type", "?")
            node_name = c.metadata.get("node_name", "?")
            start = c.metadata.get("start_line", "?")
            end = c.metadata.get("end_line", "?")
            print(
                f"  [{c.seq_num:>2}] {node_type:<12} {node_name:<30} "
                f"lines {start}-{end:<4} "
                f"orig={len(c.original_content):>5}c  embed={len(c.embedded_content):>5}c"
            )
            total_chunks += 1

    print(f"\n# {total_chunks} chunk(s) emitted across {total_files} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
