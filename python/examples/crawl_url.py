# examples/crawl_url.py
"""COPY-ME EXAMPLE — runnable depth-bounded HTTP crawl demo.

Usage:
    python examples/crawl_url.py <seed-url> [depth]

Examples:
    python examples/crawl_url.py https://example.com
    python examples/crawl_url.py https://example.com 1
    python examples/crawl_url.py https://example.com 2

Prints one line per fetched URL with byte count. This is a minimal
demonstration of chunkshop.sources.http.HttpSource — for production use, wire
it into a full chunkshop cell (chunker -> embedder -> sink) via YAML, or drive
it from a consumer loop like ``examples/sync_loop.py``.
"""
# Eager annotations (no `from __future__ import annotations`) to match the
# loading pattern used by sync_loop.py — see that file's NOTE for the reason.
import sys

from chunkshop.config import HttpSource as Cfg
from chunkshop.sources.http import HttpSource


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    seed = argv[1]
    depth = int(argv[2]) if len(argv) >= 3 else 1

    cfg = Cfg(
        type="http",
        urls=[seed],
        crawl_depth=depth,
        # Polite defaults — half-second between requests, respect robots.txt,
        # cap at 50 pages so the demo can't accidentally hammer a site.
        request_delay_seconds=0.5,
        respect_robots=True,
        max_pages=50,
    )
    src = HttpSource(cfg)

    total_bytes = 0
    n = 0
    for doc in src.iter_documents():
        n += 1
        size = len(doc.content.encode("utf-8"))
        total_bytes += size
        title = doc.title or "(no title)"
        print(f"  [{n:3d}] {size:>8d} bytes  {doc.id}  -- {title[:60]}")

    print(f"\nCrawled {n} URL(s); {total_bytes:,} total bytes from seed {seed!r} at depth={depth}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
