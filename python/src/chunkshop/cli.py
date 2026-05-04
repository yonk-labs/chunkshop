"""chunkshop CLI — ingest, orchestrate, bakeoff."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import click

from chunkshop.config import load_config
from chunkshop.runner import run_cell


@click.group()
@click.version_option(version="0.1.0", prog_name="chunkshop")
def cli():
    """Reusable ingestion tool: source -> chunker -> embedder -> extractor -> pgvector table.

    Run one cell:
        chunkshop ingest --config cell.yaml

    Run many cells in parallel:
        chunkshop orchestrate --config-dir configs/

    Rank a chunker x embedder matrix against your corpus:
        chunkshop bakeoff --config bakeoff.yaml
    """


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
