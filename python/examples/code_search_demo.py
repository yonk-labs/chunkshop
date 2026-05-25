"""End-to-end demo: symbol-aware code search + impact graph over a real repo.

Ingests a Python codebase (default: this very chunkshop repo) through the
``symbol_aware`` chunker, the ``code_relationships`` + ``code_summary``
extractors, and a pgvector sink. Then exercises three queries:

    1. ``search <text>``                  — hybrid semantic + FTS
    2. ``find-symbol <name>``             — exact / prefix symbol lookup
    3. ``impact-of <fqn> [--depth N]``    — caller / callee graph walk

Usage::

    # Ingest, then run the three sample queries.
    python examples/code_search_demo.py demo

    # Just ingest (or re-ingest after a code change).
    python examples/code_search_demo.py ingest

    # Run one query against an already-ingested cell.
    python examples/code_search_demo.py search "vector embedding"
    python examples/code_search_demo.py find-symbol SymbolAwareChunker
    python examples/code_search_demo.py impact-of \\
        chunkshop.runner.run_cell --direction both
    python examples/code_search_demo.py impact-of \\
        chunkshop.extractors.code_relationships.CodeRelationshipsExtractor.extract \\
        --depth 2 --direction callers

The demo writes into the schema ``chunkshop_codesearch_demo`` on the
postgres pointed at by ``CHUNKSHOP_TEST_DSN`` (default
``postgresql://postgres:postgres@localhost:5434/chunkshop_test``). If that
DSN is unreachable, the demo prints a hint and exits cleanly.

The demo is deliberately scoped to a small slice of the repo (``python/
src/chunkshop/*.py`` top-level files only) so ingest finishes in seconds.
Point ``--root`` at a different directory or clone target to run it against
something bigger.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

# Make the example runnable from a checkout without installing chunkshop.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Config knobs
# ---------------------------------------------------------------------------

DSN_ENV = "CHUNKSHOP_TEST_DSN"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
SCHEMA = "chunkshop_codesearch_demo"
TABLE = "chunks"
CELL_NAME = "code_search_demo"
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_dsn() -> str:
    return os.environ.get(DSN_ENV, DEFAULT_DSN)


def _pg_up(dsn: str) -> bool:
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:
        return False


def _default_corpus_root() -> Path:
    """Top-level chunkshop source dir — ~30 files, ingests in <1 minute."""
    return ROOT / "src" / "chunkshop"


def _cell_yaml(corpus_glob: str, dsn: str) -> dict:
    return {
        "cell_name": CELL_NAME,
        "source": {
            "type": "files",
            "glob": corpus_glob,
            "id_from": "stem",
        },
        "chunker": {"type": "symbol_aware", "granularity": "function"},
        "extractor": {
            "type": "composite",
            "extractors": [
                {"type": "code_relationships"},
                {"type": "code_summary", "backend": "first_n_sentences"},
            ],
        },
        "embedder": {
            "type": "fastembed",
            "model_name": DEFAULT_MODEL,
            "dim": DEFAULT_DIM,
            "batch_size": 64,
        },
        "target": {
            "type": "postgres",
            "dsn": dsn,
            "database": SCHEMA,
            "table": TABLE,
            "hnsw": False,
            "mode": "overwrite",
            "source_tag": CELL_NAME,
            "fts": {"enabled": True, "language": "english"},
            "promote_metadata": [
                {"path": "symbol_name", "type": "text"},
                {"path": "fqn", "type": "text"},
                {"path": "symbol_type", "type": "text"},
                {"path": "language", "type": "text"},
                {"path": "summary", "type": "text"},
                {"path": "start_line", "type": "int"},
                {"path": "end_line", "type": "int"},
            ],
        },
    }


def _write_cell_yaml(corpus_glob: str, dsn: str, dest: Path) -> Path:
    import yaml

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(_cell_yaml(corpus_glob, dsn), sort_keys=False))
    return dest


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> int:
    """Run the ingest cell end-to-end."""
    from chunkshop.config import CellConfig
    from chunkshop.runner import run_cell

    dsn = _resolve_dsn()
    if not _pg_up(dsn):
        print(
            f"[demo] postgres at {dsn!r} is unreachable. "
            "Start it with `docker compose -f docker-compose.test.yaml up -d` "
            "or set CHUNKSHOP_TEST_DSN.",
            file=sys.stderr,
        )
        return 2

    corpus_root = Path(args.root) if args.root else _default_corpus_root()
    if not corpus_root.exists():
        print(f"[demo] corpus root {corpus_root} does not exist", file=sys.stderr)
        return 2

    corpus_glob = str(corpus_root / "*.py")
    cell_path = ROOT / "examples" / ".code_search_demo.cell.yaml"
    _write_cell_yaml(corpus_glob, dsn, cell_path)
    print(f"[demo] corpus: {corpus_glob}")
    print(f"[demo] cell YAML: {cell_path}")
    print(f"[demo] schema:  {SCHEMA}")

    # Composite extractor doesn't forward finalize() — monkey-patch.
    # The cleaner fix is composite-level support; out of scope for the demo.
    cfg = CellConfig.model_validate_json(json.dumps(_cell_yaml(corpus_glob, dsn)))

    t0 = time.time()
    result = run_cell(cfg)

    if result.error:
        print(f"[demo] ingest FAILED: {result.error}", file=sys.stderr)
        return 1
    elapsed = time.time() - t0
    print(
        f"[demo] ingest OK in {elapsed:.1f}s — "
        f"docs={result.docs_processed} chunks={result.chunks_written}"
    )

    # Quick stats for the demo summary.
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{SCHEMA}".{TABLE}')
        n_chunks = cur.fetchone()[0]
        cur.execute(f'SELECT COUNT(DISTINCT fqn) FROM "{SCHEMA}".{TABLE}')
        n_symbols = cur.fetchone()[0]
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{SCHEMA}".code_edges')
            n_edges = cur.fetchone()[0]
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            n_edges = 0
    print(
        f"[demo] table {SCHEMA}.{TABLE}: {n_chunks} chunks across {n_symbols} symbols"
    )
    print(f"[demo] table {SCHEMA}.code_edges: {n_edges} edges")
    print(f"[demo] cell path: {cell_path}")
    return 0


def _cli_run(args: list[str]) -> int:
    """Invoke the chunkshop CLI in-process so the demo's sys.path bootstrap holds.

    Spawning ``sys.executable -m chunkshop.cli`` would re-launch a python
    interpreter that doesn't have ``ROOT/src`` on its path, so we'd need
    the package installed. To keep the demo zero-install, we call the
    Click group directly through ``CliRunner`` (its ``standalone_mode=False``
    bubbles exit codes back rather than calling ``sys.exit``).
    """
    print(f"[demo] $ chunkshop {shlex.join(args)}")
    from click.testing import CliRunner

    from chunkshop.cli import cli as chunkshop_cli

    runner = CliRunner()
    result = runner.invoke(chunkshop_cli, args, standalone_mode=False)
    if result.output:
        print(result.output, end="" if result.output.endswith("\n") else "\n")
    if result.exception and not isinstance(result.exception, SystemExit):
        # Surface the exception text without a traceback wall.
        print(f"[demo] CLI error: {result.exception}", file=sys.stderr)
    return result.exit_code if result.exit_code is not None else 0


def cmd_search(args: argparse.Namespace) -> int:
    cell_path = ROOT / "examples" / ".code_search_demo.cell.yaml"
    if not cell_path.exists():
        print("[demo] cell YAML missing — run `ingest` first.", file=sys.stderr)
        return 2
    return _cli_run([
        "search",
        "--config", str(cell_path),
        "--query", args.query,
        "--k", str(args.k),
    ])


def cmd_find_symbol(args: argparse.Namespace) -> int:
    cell_path = ROOT / "examples" / ".code_search_demo.cell.yaml"
    if not cell_path.exists():
        print("[demo] cell YAML missing — run `ingest` first.", file=sys.stderr)
        return 2
    return _cli_run([
        "search",
        "--config", str(cell_path),
        "--query", args.symbol,
        "--by-symbol", args.symbol,
        "--k", str(args.k),
    ])


def cmd_impact_of(args: argparse.Namespace) -> int:
    cell_path = ROOT / "examples" / ".code_search_demo.cell.yaml"
    if not cell_path.exists():
        print("[demo] cell YAML missing — run `ingest` first.", file=sys.stderr)
        return 2
    cli_args = [
        "impact-of",
        "--config", str(cell_path),
        "--fqn", args.fqn,
        "--direction", args.direction,
        "--depth", str(args.depth),
    ]
    if args.json_out:
        cli_args.append("--json")
    return _cli_run(cli_args)


def cmd_demo(args: argparse.Namespace) -> int:
    """Ingest then run the three sample queries back-to-back."""
    print("=" * 78)
    print("STEP 1/4: ingest")
    print("=" * 78)
    rc = cmd_ingest(args)
    if rc != 0:
        return rc

    print()
    print("=" * 78)
    print('STEP 2/4: search --query "load_extractor" (semantic + FTS)')
    print("=" * 78)
    rc = _cli_run([
        "search",
        "--config", str(ROOT / "examples" / ".code_search_demo.cell.yaml"),
        "--query", "load_extractor",
        "--k", "5",
    ])
    if rc != 0:
        print("[demo] search step failed", file=sys.stderr)
        return rc

    print()
    print("=" * 78)
    print("STEP 3/4: find-symbol SymbolAwareChunker (exact-match)")
    print("=" * 78)
    rc = _cli_run([
        "search",
        "--config", str(ROOT / "examples" / ".code_search_demo.cell.yaml"),
        "--query", "chunker",
        "--by-symbol", "SymbolAwareChunker",
        "--k", "5",
    ])
    if rc != 0:
        print("[demo] find-symbol step failed", file=sys.stderr)
        return rc

    # Pick a real FQN to walk impact for. We want one with HIGH-confidence
    # callers — the default 0.7 floor drops ambiguous-name edges (multiple
    # corpus symbols share a bare name), so an FQN with only 0.5-confidence
    # callers would render a misleading "no edges found".
    import psycopg

    dsn = _resolve_dsn()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT dst_fqn, COUNT(*) AS callers '
            f'FROM "{SCHEMA}".code_edges '
            f'WHERE edge_type = %s AND confidence >= 0.7 '
            f'GROUP BY dst_fqn ORDER BY callers DESC LIMIT 5',
            ("CALLS",),
        )
        rows = cur.fetchall()
    if not rows:
        print("[demo] no high-confidence CALLS edges found — skipping impact step", file=sys.stderr)
        return 0
    top_fqn, n_callers = rows[0]
    print()
    print("=" * 78)
    print(f"STEP 4/4: impact-of {top_fqn}  ({n_callers} callers)")
    print("=" * 78)
    rc = _cli_run([
        "impact-of",
        "--config", str(ROOT / "examples" / ".code_search_demo.cell.yaml"),
        "--fqn", top_fqn,
        "--direction", "both",
        "--depth", "2",
    ])
    print()
    print("[demo] all four steps OK.")
    return rc


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="chunkshop SP-E end-to-end code-search demo",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest the corpus into Postgres")
    p_ingest.add_argument("--root", default=None, help="Corpus root directory (default: chunkshop's own src/)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_search = sub.add_parser("search", help="Hybrid search the ingested corpus")
    p_search.add_argument("query", help="Free-text query")
    p_search.add_argument("--k", type=int, default=5)
    p_search.set_defaults(func=cmd_search)

    p_find = sub.add_parser("find-symbol", help="Find chunks for a symbol by name")
    p_find.add_argument("symbol", help="Symbol name (e.g. BaseConnector). Trailing * = LIKE prefix.")
    p_find.add_argument("--k", type=int, default=10)
    p_find.set_defaults(func=cmd_find_symbol)

    p_impact = sub.add_parser("impact-of", help="Walk callers/callees of an FQN")
    p_impact.add_argument("fqn", help="Fully-qualified symbol name to walk from")
    p_impact.add_argument("--depth", type=int, default=1)
    p_impact.add_argument(
        "--direction", choices=["callers", "callees", "both"], default="callers"
    )
    p_impact.add_argument("--json", dest="json_out", action="store_true", help="JSON output")
    p_impact.set_defaults(func=cmd_impact_of)

    p_demo = sub.add_parser("demo", help="Ingest then run all three sample queries")
    p_demo.add_argument("--root", default=None, help="Corpus root (default: chunkshop's own src/)")
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
