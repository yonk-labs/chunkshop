"""chunkshop CLI — ingest, orchestrate, bakeoff, prefetch."""
from __future__ import annotations
import json
import logging
import os
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

import click

from chunkshop.config import load_config
from chunkshop.runner import run_cell


def _setup_cli_logging(json_format: bool = False) -> None:
    """Configure stdout logging for CLI subcommands.

    Library users importing chunkshop see no log output by default — they
    configure their own root logger. CLI users see chunkshop's progress lines
    on stdout. Idempotent: safe to call multiple times in one process.
    """
    chunkshop_logger = logging.getLogger("chunkshop")
    # If a handler is already attached (e.g. the orchestrator-spawned subprocess
    # also configures stdout), don't double it up.
    if chunkshop_logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        # Minimal hand-rolled JSON formatter — avoids pulling python-json-logger
        # into the base install. Library users wanting richer JSON can wire
        # their own handler.
        class _JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
                return json.dumps({
                    "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                })
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    chunkshop_logger.addHandler(handler)
    chunkshop_logger.setLevel(logging.INFO)
    chunkshop_logger.propagate = False  # don't double-print via root logger


@click.group()
@click.version_option(version=_pkg_version("chunkshop"), prog_name="chunkshop")
def cli():
    """Reusable ingestion tool: source -> chunker -> embedder -> extractor -> pgvector table.

    Run one cell:
        chunkshop ingest --config cell.yaml

    Run many cells in parallel:
        chunkshop orchestrate --config-dir configs/

    Rank a chunker x embedder matrix against your corpus:
        chunkshop bakeoff --config bakeoff.yaml

    Pre-download the embedder model so the first ingest never blocks:
        chunkshop prefetch --config cell.yaml
    """


_INIT_BACKEND_TEMPLATES = {
    "postgres": """target:
  type: postgres
  dsn_env: CHUNKSHOP_DSN
  database: {db}        # mapped to PG SCHEMA at the sink
  table: chunks
  mode: overwrite
  hnsw: true
  source_tag: {tag}""",
    "mariadb": """target:
  type: mariadb
  dsn_env: CHUNKSHOP_DSN_MARIADB
  database: {db}        # MariaDB DATABASE (not just the connection default)
  table: chunks
  mode: overwrite
  source_tag: {tag}""",
    "sqlite": """target:
  type: sqlite
  dsn_env: SQLITE_PATH  # env var holds the .db file path (or :memory:)
  database: ignored     # SQLite has no schema namespace; field required but ignored at runtime
  table: chunks
  mode: overwrite
  source_tag: {tag}""",
    "clickhouse": """target:
  type: clickhouse
  dsn_env: CHUNKSHOP_DSN_CH
  database: {db}
  table: chunks
  mode: overwrite
  source_tag: {tag}
  # For re-ingest deduplication, uncomment the next line:
  # engine: "ReplacingMergeTree(created_at) ORDER BY (id)"
""",
}


@cli.command()
@click.option(
    "--out", "out_path",
    type=click.Path(path_type=Path), default=Path("cell.yaml"),
    help="Output path for the generated cell YAML (default: cell.yaml).",
)
@click.option(
    "--force", is_flag=True,
    help="Overwrite the output file if it already exists.",
)
def init(out_path: Path, force: bool):
    """Interactive scaffold for a new chunkshop cell YAML.

    Prompts for the backend, corpus glob, chunker, embedder, and emits a
    runnable cell config. Pair with `chunkshop validate` to dry-run the
    config without opening DB connections.
    """
    if out_path.exists() and not force:
        click.echo(f"[init] {out_path} already exists — pass --force to overwrite", err=True)
        sys.exit(1)

    cell_name = click.prompt("Cell name", default="my_cell")
    backend = click.prompt(
        "Backend",
        type=click.Choice(list(_INIT_BACKEND_TEMPLATES.keys())),
        default="postgres",
    )
    corpus = click.prompt("Corpus path (glob, e.g. ./docs/*.md)", default="./docs/*.md")
    chunker = click.prompt(
        "Chunker",
        type=click.Choice(["hierarchy", "sentence_aware", "fixed_overlap"]),
        default="hierarchy",
    )
    model = click.prompt(
        "Embedder model",
        default="Xenova/bge-small-en-v1.5-int8",
    )
    dim = click.prompt("Embedder dim", type=int, default=384)
    database = click.prompt(
        "Target database/schema name",
        default=f"chunkshop_{cell_name}",
    )
    source_tag = click.prompt("source_tag", default=cell_name)

    target_block = _INIT_BACKEND_TEMPLATES[backend].format(db=database, tag=source_tag)

    yaml_text = f"""cell_name: {cell_name}

source:
  type: files
  glob: "{corpus}"
  id_from: stem

framer:
  type: identity

chunker:
  type: {chunker}

embedder:
  type: fastembed
  model_name: {model}
  dim: {dim}
  batch_size: 64

{target_block}
"""
    out_path.write_text(yaml_text)
    click.echo(f"[init] wrote {out_path}")
    click.echo(f"[init] next steps:")
    click.echo(f"  1. Set the DSN env var for {backend} (see docs/engines/{backend}.md)")
    click.echo(f"  2. chunkshop validate --config {out_path}")
    click.echo(f"  3. chunkshop ingest --config {out_path}")


@cli.command()
@click.option(
    "--config", "-c", required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the YAML/JSON cell config.",
)
def validate(config: Path):
    """Validate a YAML config without running it. Exits 0 if valid, non-zero otherwise.

    Loads the config, runs pydantic validation, and reports the resolved
    source/chunker/embedder/target shape. Does NOT open DB connections or
    create tables — useful for fast iteration on config edits.
    """
    try:
        cfg = load_config(config)
    except Exception as e:
        click.echo(f"[validate] FAIL: {e}", err=True)
        sys.exit(1)
    click.echo(f"[validate] OK — cell {cfg.cell_name!r}")
    click.echo(f"  source:   {cfg.source.type}")
    click.echo(f"  framer:   {cfg.framer.type}")
    click.echo(f"  chunker:  {cfg.chunker.type}")
    click.echo(f"  embedder: {cfg.embedder.model_name} (dim={cfg.embedder.dim})")
    click.echo(f"  extractor:{cfg.extractor.type}")
    click.echo(f"  target:   {cfg.target.type} -> {cfg.target.database_name}.{cfg.target.table} (mode={cfg.target.mode})")


@cli.command()
@click.option(
    "--config", "-c", required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the YAML/JSON cell config.",
)
@click.option(
    "--doc-limit", type=int, default=None,
    help="Override runtime.doc_limit in the YAML (useful for smoke tests).",
)
@click.option(
    "--log", "log_path",
    type=click.Path(path_type=Path), default=None,
    help="Override runtime.log_path in the YAML.",
)
@click.option(
    "--omp-threads", type=int, default=None,
    help="Override OMP_NUM_THREADS. Default from YAML (usually 1).",
)
def ingest(config: Path, doc_limit, log_path, omp_threads):
    """Run one cell end-to-end: read source -> chunk -> embed -> extract tags -> write to pgvector table."""
    cfg = load_config(config)
    _setup_cli_logging(json_format=(getattr(cfg.runtime, "log_format", "text") == "json"))
    if doc_limit is not None:
        cfg.runtime.doc_limit = doc_limit
    if log_path is not None:
        cfg.runtime.log_path = str(log_path)
    if omp_threads is not None:
        cfg.runtime.omp_num_threads = omp_threads
    result = run_cell(cfg)
    click.echo(json.dumps({
        "cell_name": result.cell_name,
        "docs_processed": result.docs_processed,
        "chunks_written": result.chunks_written,
        "wall_seconds": round(result.wall_seconds, 2),
        "error": result.error,
    }, indent=2))
    sys.exit(1 if result.error else 0)


@cli.command()
@click.option(
    "--config", "-c", required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the YAML/JSON cell config whose embedder model is fetched.",
)
def prefetch(config: Path):
    """Download the embedder model named in a config so first ingest never blocks.

    fastembed lazily fetches the ONNX model from HuggingFace on first embed().
    Run this once (in a Dockerfile, CI step, or setup script) to make installs
    batteries-included — the multi-second download happens here, explicitly,
    instead of silently inside the first ingest / library store() call.

    Offline: export HF_HUB_OFFLINE=1 to fail fast if the model is not already
    cached, rather than attempting a network fetch.
    """
    from chunkshop.embedders import load_embedder

    cfg = load_config(config)
    _setup_cli_logging(json_format=(getattr(cfg.runtime, "log_format", "text") == "json"))
    model_name = getattr(cfg.embedder, "model_name", "<unknown>")
    click.echo(f"[prefetch] fetching embedder model {model_name!r} ...")
    load_embedder(cfg.embedder)  # constructs the provider → fastembed caches the ONNX model
    click.echo(f"[prefetch] model {model_name!r} is cached and ready.")


@cli.command()
@click.option(
    "--config-dir", "-d",
    type=click.Path(exists=True, path_type=Path), default=None,
    help="Directory of YAML configs; every *.yaml/*.yml runs as one cell.",
)
@click.option(
    "--config", "-c",
    type=click.Path(exists=True, path_type=Path), multiple=True,
    help="Explicit YAML paths (repeatable). Mutually exclusive with --config-dir.",
)
@click.option(
    "--concurrency", type=int, default=4, show_default=True,
    help="Max parallel cells.",
)
@click.option(
    "--checkpoints", default="60,120,300,600", show_default=True,
    help="Comma-separated seconds at which to emit a status report.",
)
@click.option(
    "--timeout", type=int, default=2 * 60 * 60, show_default=True,
    help="Overall timeout in seconds before killing surviving workers.",
)
@click.option(
    "--smoke/--full", default=False,
    help="Smoke mode: force --doc-limit=1 and concurrency=1.",
)
def orchestrate(config_dir, config, concurrency, checkpoints, timeout, smoke):
    """Run N cells in parallel, emit checkpoint reports at t=60/120/300/600s by default."""
    _setup_cli_logging()
    from chunkshop.orchestrator import orchestrate as _orch

    if config_dir and config:
        raise click.UsageError("--config-dir and --config are mutually exclusive")
    if not config_dir and not config:
        raise click.UsageError("provide --config-dir or one or more --config")

    if config_dir:
        paths = sorted(
            [p for p in config_dir.glob("*.yaml")] + [p for p in config_dir.glob("*.yml")]
        )
    else:
        paths = list(config)

    if not paths:
        raise click.UsageError("no YAML configs found")

    if smoke:
        concurrency = 1
        # Rewrite each YAML in a tmp copy with doc_limit=1
        import tempfile
        import yaml as _yaml
        tmp = Path(tempfile.mkdtemp(prefix="chunkshop-smoke-"))
        new_paths = []
        for p in paths:
            data = _yaml.safe_load(p.read_text())
            data.setdefault("runtime", {})["doc_limit"] = 1
            out = tmp / p.name
            out.write_text(_yaml.safe_dump(data, sort_keys=False))
            new_paths.append(out)
        paths = new_paths

    cp_list = [int(x) for x in checkpoints.split(",") if x.strip()]
    result = _orch(
        configs=paths,
        concurrency=concurrency,
        checkpoint_seconds=cp_list,
        overall_timeout_seconds=timeout,
    )
    click.echo(json.dumps({
        "total": result.total,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "cells": result.cells,
    }, indent=2))
    sys.exit(1 if result.failed else 0)


@cli.command()
@click.option(
    "--config", "config_path",
    required=True, type=click.Path(exists=True, path_type=Path),
    help="Path to the bakeoff YAML config (see docs/samples/bakeoff.yaml).",
)
@click.option(
    "--yes", is_flag=True,
    help="Bypass the >50-cell matrix confirmation prompt.",
)
def bakeoff(config_path: Path, yes: bool):
    """Run a multi-backend chunker x embedder matrix bakeoff against a corpus.

    Each entry under `targets:` in the YAML produces one full pass of the
    chunker x embedder matrix into that backend. Every cell is queried via
    its sink's native vector syntax; the report shows MRR + ingest/query
    wall time side by side per backend so accuracy parity (or divergence)
    and performance differences are directly comparable.

    The DSN env vars named in each `targets[].dsn_env` must be exported
    before running. The bakeoff does NOT clean up databases / schemas /
    files after the run — that's the user's responsibility.

    Outputs (under output_dir, default `skill-output/bakeoff/{name}/`):
      - results.json      — raw scored data
      - report.md         — multi-backend leaderboard + per-query detail
      - recommended.yaml  — runnable `chunkshop ingest` cell for the top combo
    """
    _setup_cli_logging()
    import yaml

    from chunkshop.bakeoff.config import BakeoffConfig
    from chunkshop.bakeoff.output import (
        write_recommended_yaml,
        write_report_md,
        write_results_json,
    )
    from chunkshop.bakeoff.runner import run_bakeoff

    cfg = BakeoffConfig.model_validate(yaml.safe_load(config_path.read_text()))

    n_embedders = len(cfg.matrix.embedders)
    n_chunkers = len(cfg.matrix.chunkers)
    n_targets = len(cfg.targets)
    n_combos = n_embedders * n_chunkers * n_targets
    if n_combos > 50 and not yes:
        click.echo(
            f"WARNING: {n_combos} cells is large "
            f"({n_embedders} embedders x {n_chunkers} chunkers x {n_targets} backends). "
            "Each cell ingests the full corpus into its own table."
        )
        if not click.confirm("Proceed?", default=False):
            click.echo("Aborted.")
            raise click.Abort()

    # Verify every DSN env var is set before doing any work.
    for tgt in cfg.targets:
        if tgt.dsn_env not in os.environ:
            raise click.UsageError(
                f"DSN env var {tgt.dsn_env!r} for target type={tgt.type!r} "
                "is not set. Export it before running `chunkshop bakeoff`."
            )

    backend_summary = ", ".join(t.type for t in cfg.targets)
    click.echo(
        f"Running bakeoff '{cfg.name}' — {n_combos} cells "
        f"({n_embedders}×{n_chunkers} matrix × {n_targets} backends: {backend_summary})"
    )
    results = run_bakeoff(cfg)

    out_dir = Path(cfg.output_dir or f"skill-output/bakeoff/{cfg.name}")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = write_results_json(results, out_dir)
    md_path = write_report_md(cfg, results, out_dir)
    yaml_path = write_recommended_yaml(cfg, results, out_dir)

    # Top-line summary: rank-1 combo + output paths.
    ranked = sorted(results.combos, key=lambda c: -c.aggregate.get("mrr", 0))
    top = ranked[0]
    click.echo("")
    click.echo(
        f"Winner: [{top.backend}] {top.chunker_label} + {top.embedder_label} "
        f"(MRR={top.aggregate.get('mrr', 0):.3f}, "
        f"r@1={top.aggregate.get('recall_at_1', 0):.3f})"
    )
    click.echo(f"Results: {json_path}")
    click.echo(f"Report:  {md_path}")
    click.echo(f"Recommended cell: {yaml_path}")


if __name__ == "__main__":
    cli()
