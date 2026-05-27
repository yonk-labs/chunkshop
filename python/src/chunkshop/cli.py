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

    Detects config shape from the YAML doc — an ingest cell vs a
    `chunkshop bakeoff` matrix config — and dispatches to the right
    pydantic schema. Reports the resolved shape on success. Does NOT
    open DB connections or create tables — useful for fast iteration
    on config edits.

    Closes #10 — prior behavior was to assume ingest-cell shape and
    surface a wall of `extra_forbidden` errors when given a bakeoff
    config. Now both shapes validate cleanly via the same CLI.
    """
    import yaml
    try:
        doc = yaml.safe_load(config.read_text())
    except Exception as e:
        click.echo(f"[validate] FAIL: {e}", err=True)
        sys.exit(1)

    if not isinstance(doc, dict):
        click.echo(
            f"[validate] FAIL: top-level YAML must be a mapping, got "
            f"{type(doc).__name__}",
            err=True,
        )
        sys.exit(1)

    # Bakeoff configs are identified by the two fields that ONLY exist
    # in BakeoffConfig (and never in CellConfig): `matrix` and `gold_queries`.
    is_bakeoff = "matrix" in doc and "gold_queries" in doc

    if is_bakeoff:
        from chunkshop.bakeoff.config import BakeoffConfig
        try:
            cfg = BakeoffConfig.model_validate(doc)
        except Exception as e:
            click.echo(f"[validate] FAIL (bakeoff config): {e}", err=True)
            sys.exit(1)
        n_embedders = len(cfg.matrix.embedders)
        n_chunkers = len(cfg.matrix.chunkers)
        n_targets = len(cfg.targets)
        click.echo(f"[validate] OK (bakeoff config) — {cfg.name!r}")
        click.echo(f"  source:   {cfg.source.type}")
        click.echo(
            f"  matrix:   {n_embedders} embedders × {n_chunkers} chunkers "
            f"× {n_targets} targets = {n_embedders * n_chunkers * n_targets} combos"
        )
        click.echo(f"  targets:  {[t.type for t in cfg.targets]}")
        return

    # Default path: ingest cell.
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
      - report.md         — multi-target leaderboard + per-query detail
      - recommended.yaml  — runnable `chunkshop ingest` cell for the top combo
    """
    _setup_cli_logging()
    import yaml

    from chunkshop.bakeoff.config import BakeoffConfig
    from chunkshop.bakeoff.keys import target_display_keys
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
            f"({n_embedders} embedders x {n_chunkers} chunkers x {n_targets} targets). "
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

    target_summary = ", ".join(target_display_keys(cfg.targets))
    click.echo(
        f"Running bakeoff '{cfg.name}' — {n_combos} cells "
        f"({n_embedders}×{n_chunkers} matrix × {n_targets} targets: {target_summary})"
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


@cli.group(name="eval")
def eval_cmd():
    """Internal RAG evaluation harness planning commands."""


@eval_cmd.command(name="validate")
@click.option(
    "--config", "config_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to an eval matrix YAML config.",
)
def eval_validate(config_path: Path):
    """Validate an eval matrix without running retrieval or judges."""
    from chunkshop.eval import build_eval_plan, load_eval_matrix

    try:
        cfg = load_eval_matrix(config_path)
        plan = build_eval_plan(cfg)
    except Exception as exc:
        raise click.ClickException(str(exc))
    click.echo(f"[eval] OK — {cfg.name!r}")
    click.echo(f"  workloads: {len(cfg.workloads)}")
    click.echo(f"  baselines: {len(cfg.baselines)}")
    click.echo(f"  explicit candidates: {len(cfg.candidates)}")
    click.echo(f"  profiles: {len(cfg.profiles)}")
    click.echo(f"  expanded policies: {len(plan.policies)}")
    click.echo(f"  planned runs: {len(plan.runs)}")


@eval_cmd.command(name="plan")
@click.option(
    "--config", "config_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to an eval matrix YAML config.",
)
@click.option(
    "--out", "out_dir",
    type=click.Path(path_type=Path),
    default=Path("skill-output/eval/plan"),
    show_default=True,
    help="Directory for manifest.json, report.md, and generated llm-judge configs.",
)
@click.option(
    "--profile", "profiles",
    multiple=True,
    help="Easy-mode/profile name to expand. Repeatable. Defaults to all profiles.",
)
@click.option(
    "--tag", "tags",
    multiple=True,
    help="Only include workloads/policies carrying at least one tag. Repeatable.",
)
@click.option(
    "--smoke-limit",
    type=int,
    default=12,
    show_default=True,
    help="Limit inserted into generated smoke-mode llm-judge configs.",
)
def eval_plan(config_path: Path, out_dir: Path, profiles, tags, smoke_limit: int):
    """Expand an eval matrix into a concrete execution manifest.

    This does not run ingestion, retrieval, answer generation, or judging. It
    creates the stable artifact a runner can execute and review.
    """
    from chunkshop.eval import build_eval_plan, load_eval_matrix, write_eval_plan

    try:
        cfg = load_eval_matrix(config_path)
        plan = build_eval_plan(
            cfg,
            profiles=list(profiles) or None,
            tags=list(tags) or None,
        )
        plan = write_eval_plan(cfg, plan, out_dir, smoke_limit=smoke_limit)
    except Exception as exc:
        raise click.ClickException(str(exc))
    click.echo(f"[eval] wrote plan for {cfg.name!r}")
    click.echo(f"  manifest: {out_dir / 'manifest.json'}")
    click.echo(f"  report:   {out_dir / 'report.md'}")
    click.echo(f"  workloads: {len(plan.workloads)}")
    click.echo(f"  policies:  {len(plan.policies)}")
    click.echo(f"  runs:      {len(plan.runs)}")
    if plan.llm_judge_configs:
        click.echo(f"  llm-judge configs: {len(plan.llm_judge_configs)}")


def _parse_where(opts) -> dict:
    """Parse ``--where KEY=VALUE`` opts into a structured filter dict.

    Supported keys:
      ``source=<tag>``         — filter by source_tag column.
      ``tags=a,b``             — filter to chunks whose tags array overlaps [a, b].
      ``metadata.<key>=<val>`` — filter by a jsonb metadata key.

    Raises ``ValueError`` on unrecognised keys or missing ``=``.
    """
    where: dict = {}
    for item in opts:
        if "=" not in item:
            raise ValueError(f"--where must be KEY=VALUE, got {item!r}")
        key, val = item.split("=", 1)
        if key == "tags":
            where["tags"] = val.split(",")
        elif key == "source":
            where["source"] = val
        elif key.startswith("metadata."):
            where.setdefault("metadata", {})[key[len("metadata."):]] = val
        else:
            raise ValueError(f"unsupported --where key: {key!r}")
    return where


def _parse_by_symbol(value: str) -> tuple[list[str], list[str]]:
    """Parse a ``--by-symbol`` argument into (exact_names, like_patterns).

    Accepts comma-separated names. A name ending in ``*`` (or containing the
    SQL glob ``%``) is routed to the LIKE list with the appropriate pattern;
    the rest are exact-match candidates folded into an ``IN (...)`` predicate.

    Example::

        "BaseConnector,HttpSource*"
          -> (["BaseConnector"], ["HttpSource%"])
    """
    exact: list[str] = []
    like: list[str] = []
    for raw in value.split(","):
        name = raw.strip()
        if not name:
            continue
        if name.endswith("*"):
            like.append(name[:-1] + "%")
        elif "%" in name:
            like.append(name)
        else:
            exact.append(name)
    return exact, like


@cli.command()
@click.option(
    "--config", required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the YAML/JSON cell config.",
)
@click.option("--query", required=True, help="Free-text query string.")
@click.option("--k", default=10, type=int, show_default=True,
              help="Number of results to return.")
@click.option(
    "--return", "return_mode",
    type=click.Choice(["chunks", "summary+chunks", "summary"]),
    default="chunks", show_default=True,
    help="What the result carries: fused hit list, summary, or both.",
)
@click.option(
    "--legs", default="semantic,fts", show_default=True,
    help="Comma-separated retrieval legs (semantic, fts).",
)
@click.option(
    "--vector-metric",
    type=click.Choice(["cosine", "inner_product", "l2"]),
    default=None,
    help="Postgres pgvector metric for semantic search. Defaults to target.vector_metric.",
)
@click.option(
    "--where", "where_opts", multiple=True,
    help="Filter as KEY=VALUE (source=x, tags=a,b, metadata.k=v). Repeatable.",
)
@click.option(
    "--by-symbol", "by_symbol",
    default=None,
    help=(
        "Narrow results to chunks whose symbol_name matches. Comma-separated "
        "names act as IN(...). A trailing '*' or '%' enables LIKE prefix-match. "
        "Requires the cell's target.promote_metadata to surface symbol_name "
        "as a real column (see docs/cookbook/code-search.md)."
    ),
)
@click.option("--json", "as_json", is_flag=True,
              help="Emit results as JSON instead of human-readable text.")
@click.option(
    "--compress", "compress", is_flag=True, default=False,
    help=(
        "Strip fluff words from the summary via the caveman reducer "
        "(only affects --return summary/summary+chunks)."
    ),
)
def search(config, query, k, return_mode, legs, vector_metric, where_opts, by_symbol, as_json, compress):
    """Hybrid-search a cell's target; optionally summarize the hits.

    Embeds the query with the cell's configured embedder, runs a hybrid
    semantic + full-text search against the cell's target table, and prints
    the results. Use --return to choose between raw chunks, a summary, or
    both. Errors exit non-zero with a plain message, no traceback.

    Examples:

    \b
        chunkshop search --config cell.yaml --query "pgvector cosine search"
        chunkshop search --config cell.yaml --query "alpha" --k 5 --json
        chunkshop search --config cell.yaml --query "alpha" \\
            --return summary+chunks --where source=infra
        chunkshop search --config cell.yaml --query "iter changes" \\
            --by-symbol HttpSource
    """
    import json as _json

    import yaml as _yaml

    from chunkshop.config import CellConfig
    from chunkshop.embedders import load_embedder
    from chunkshop.search_common import search as _search

    try:
        cfg = CellConfig.model_validate(_yaml.safe_load(Path(config).read_text()))
        emb = load_embedder(cfg.embedder)
        qv = emb.embed([query])[0]
        tgt = cfg.target

        summarize_fn = None
        if return_mode != "chunks":
            from chunkshop.summarizers.lede import summarize as summarize_fn  # type: ignore[assignment]

        compress_fn = None
        if compress and return_mode != "chunks":
            from chunkshop.summarizers.caveman import summarize as compress_fn  # type: ignore[assignment]

        parsed_where = _parse_where(where_opts) or {}

        if by_symbol:
            exact, like = _parse_by_symbol(by_symbol)
            if not exact and not like:
                raise click.UsageError(
                    "--by-symbol must include at least one name (got empty after parsing)"
                )
            if exact:
                parsed_where.setdefault("column_in", {})["symbol_name"] = exact
            # SQL LIKE on a column allows one pattern per call — for multiple
            # globs we'd need OR, but Click users typically supply one prefix.
            # If they supplied many, take the first; the rest is an exact-match
            # contract. The CLI documents the comma-separated shape as
            # IN()-style; trailing-star is the documented wildcard.
            if like:
                if len(like) == 1:
                    parsed_where["column_like"] = {"symbol_name": like[0]}
                else:
                    # Multiple wildcards: fall back to exact-match for each LIKE
                    # pattern by stripping the trailing %. Better than silently
                    # ignoring the rest.
                    parsed_where.setdefault("column_in", {})
                    parsed_where["column_in"].setdefault("symbol_name", []).extend(
                        [p.rstrip("%") for p in like]
                    )

        res = _search(
            tgt.resolve_dsn(),
            schema=tgt.database_name,
            table=tgt.table,
            query=query,
            query_vec=qv,
            k=k,
            legs=tuple(legs.split(",")),
            where=parsed_where or None,
            return_mode=return_mode,
            summarize_fn=summarize_fn,
            language=(tgt.fts.language if tgt.fts else "english"),
            vector_metric=vector_metric or tgt.vector_metric,
            compress_fn=compress_fn,
        )
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(str(exc))

    if as_json:
        click.echo(_json.dumps({
            "query": res.query,
            "summary": res.summary,
            "chunks": [
                {
                    "doc_id": h.doc_id,
                    "seq_num": h.seq_num,
                    "score": h.score,
                    "text": h.text,
                    "legs": list(h.legs),
                    "metadata": h.metadata,
                }
                for h in res.chunks
            ],
        }, indent=2))
    else:
        if res.summary:
            click.echo(f"SUMMARY:\n{res.summary}\n")
        for i, h in enumerate(res.chunks, 1):
            sym = h.metadata.get("symbol_name") if h.metadata else None
            fqn = h.metadata.get("fqn") if h.metadata else None
            path = (
                h.metadata.get("path")
                or h.metadata.get("source_path")
                or h.metadata.get("file_path")
                if h.metadata
                else None
            )
            suffix = ""
            if sym or fqn or path:
                bits = []
                if sym:
                    bits.append(f"symbol={sym}")
                if fqn and fqn != sym:
                    bits.append(f"fqn={fqn}")
                if path:
                    bits.append(f"path={path}")
                suffix = "  " + " ".join(bits)
            click.echo(
                f"{i}. [{h.score:.4f}] {h.doc_id}#{h.seq_num}  {h.text[:120]}{suffix}"
            )


# ---------------------------------------------------------------------------
# impact-of subcommand (SP-E)
# ---------------------------------------------------------------------------

# Hard cap on recursive-CTE depth so a malformed CLI invocation can't ask
# Postgres to walk an arbitrary number of hops. 10 is comfortably past any
# real-world impact-of question (the call graph rarely exceeds 5 hops in
# human-readable explanations) while bounding worst-case fanout cost.
_IMPACT_MAX_DEPTH = 10

# Allowlist regex for the project_id parameter. We bind it as a query
# parameter so injection isn't the concern — but a stray uppercase / space
# usually means a misconfigured cell, and a clear early error beats an empty
# result set.
_IMPACT_PROJECT_RE = __import__("re").compile(r"^[A-Za-z0-9_.\-]+$")


def _impact_query_one_direction(
    dsn: str,
    *,
    schema: str,
    fqn: str,
    direction: str,
    depth: int,
    project_id: str,
    confidence_floor: float,
    edge_type: str,
) -> list[dict]:
    """Walk the code_edges table N hops from ``fqn`` and return rows.

    direction='callers' walks dst -> src; direction='callees' walks src -> dst.

    Returns one dict per discovered FQN with keys::

      {"fqn", "hop", "confidence", "evidence", "src_fqn", "dst_fqn",
       "src_node_id", "dst_node_id"}

    Schema/table identifiers are spliced (psycopg cannot bind them) but
    validated by :func:`chunkshop.extractors.code_relationships._validate_ident`
    before this function is called.
    """
    import psycopg
    from psycopg import sql

    from chunkshop.extractors.code_relationships import _validate_ident

    _validate_ident(schema, kind="schema")
    if direction not in ("callers", "callees"):
        raise ValueError(f"direction must be 'callers' or 'callees', got {direction!r}")
    if not (1 <= depth <= _IMPACT_MAX_DEPTH):
        raise ValueError(
            f"depth must be between 1 and {_IMPACT_MAX_DEPTH}, got {depth}"
        )

    schema_id = sql.Identifier(schema)
    table_id = sql.Identifier("code_edges")
    fq = sql.SQL("{}.{}").format(schema_id, table_id)

    # callers: walk DST=fqn -> SRC; callees: walk SRC=fqn -> DST.
    if direction == "callers":
        anchor_col = "dst_fqn"
        next_col = "src_fqn"
        next_id_col = "src_node_id"
    else:
        anchor_col = "src_fqn"
        next_col = "dst_fqn"
        next_id_col = "dst_node_id"

    # Recursive CTE: anchor row at hop=1, then expand via the next direction.
    # Stop when hop reaches depth (bound by Python before splicing, so it's
    # always a safe literal — but we cast to int defensively).
    cte = sql.SQL(
        "WITH RECURSIVE walk AS ("
        " SELECT {next_col} AS fqn, {next_id_col} AS node_id, edge_type, confidence,"
        "        evidence, src_fqn, dst_fqn, 1::int AS hop"
        " FROM {fq}"
        " WHERE project_id = %s AND edge_type = %s AND {anchor_col} = %s"
        "       AND confidence >= %s"
        " UNION ALL"
        " SELECT e.{next_col} AS fqn, e.{next_id_col} AS node_id, e.edge_type,"
        "        e.confidence, e.evidence, e.src_fqn, e.dst_fqn, w.hop + 1"
        " FROM {fq} e"
        " JOIN walk w ON w.fqn = e.{anchor_col}"
        " WHERE e.project_id = %s AND e.edge_type = %s AND e.confidence >= %s"
        "       AND w.hop < {depth}"
        ")"
        " SELECT fqn, MIN(hop) AS hop, MAX(confidence) AS confidence,"
        " (ARRAY_AGG(evidence ORDER BY confidence DESC))[1] AS evidence,"
        " (ARRAY_AGG(src_fqn ORDER BY confidence DESC))[1] AS src_fqn,"
        " (ARRAY_AGG(dst_fqn ORDER BY confidence DESC))[1] AS dst_fqn"
        " FROM walk GROUP BY fqn ORDER BY hop ASC, confidence DESC"
    ).format(
        fq=fq,
        next_col=sql.Identifier(next_col),
        next_id_col=sql.Identifier(next_id_col),
        anchor_col=sql.Identifier(anchor_col),
        depth=sql.Literal(int(depth)),
    )

    params = (
        project_id, edge_type, fqn, confidence_floor,
        project_id, edge_type, confidence_floor,
    )

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(cte, params)
        rows = cur.fetchall()

    return [
        {
            "fqn": r[0],
            "hop": int(r[1]),
            "confidence": float(r[2]),
            "evidence": r[3] or {},
            "src_fqn": r[4],
            "dst_fqn": r[5],
        }
        for r in rows
    ]


def _enrich_with_chunk_metadata(
    dsn: str,
    *,
    schema: str,
    table: str,
    rows: list[dict],
) -> list[dict]:
    """Best-effort join: enrich each row with file_path + line range + summary.

    Looks up the chunk(s) whose ``fqn`` column matches. Skips silently if the
    chunks table doesn't carry an ``fqn`` column (the user didn't promote it).
    """
    if not rows:
        return rows
    import psycopg
    from psycopg import sql

    fqns = list({r["fqn"] for r in rows})

    schema_id = sql.Identifier(schema)
    table_id = sql.Identifier(table)
    fq = sql.SQL("{}.{}").format(schema_id, table_id)

    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            # First check: does the table have an fqn column?
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, table),
            )
            cols = {row[0] for row in cur.fetchall()}
            if "fqn" not in cols:
                return rows
            has_path = "path" in cols
            has_start = "start_line" in cols
            has_end = "end_line" in cols
            has_summary = "summary" in cols
            select_parts = ["fqn"]
            if has_path:
                select_parts.append("path")
            if has_start:
                select_parts.append("start_line")
            if has_end:
                select_parts.append("end_line")
            if has_summary:
                select_parts.append("summary")
            cur.execute(
                sql.SQL(
                    "SELECT {cols} FROM {fq} WHERE fqn = ANY(%s) "
                    "ORDER BY seq_num ASC"
                ).format(
                    cols=sql.SQL(", ").join(sql.Identifier(c) for c in select_parts),
                    fq=fq,
                ),
                (fqns,),
            )
            # Use the FIRST row for each FQN (chunks are seq_num-ordered).
            by_fqn: dict[str, dict] = {}
            for r in cur.fetchall():
                idx = 1
                payload = {}
                if has_path:
                    payload["path"] = r[idx]
                    idx += 1
                if has_start:
                    payload["start_line"] = r[idx]
                    idx += 1
                if has_end:
                    payload["end_line"] = r[idx]
                    idx += 1
                if has_summary:
                    payload["summary"] = r[idx]
                    idx += 1
                by_fqn.setdefault(r[0], payload)
    except Exception:
        # The enrichment is best-effort; never let it break the main query.
        return rows

    for r in rows:
        extra = by_fqn.get(r["fqn"])
        if extra:
            r.update(extra)
    return rows


def _render_impact_text(target: str, callers: list[dict], callees: list[dict]) -> str:
    lines = [f"impact-of: {target}"]
    if callers:
        lines.append("")
        lines.append("CALLERS:")
        for r in callers:
            prefix = "  " * r["hop"]
            path = r.get("path")
            range_bit = ""
            if path:
                range_bit = f"  ({path}"
                if r.get("start_line") and r.get("end_line"):
                    range_bit += f":{r['start_line']}-{r['end_line']}"
                range_bit += ")"
            lines.append(
                f"{prefix}- [{r['confidence']:.2f}] {r['fqn']} (hop={r['hop']}){range_bit}"
            )
            if r.get("summary"):
                lines.append(f"{prefix}    {r['summary']}")
    if callees:
        lines.append("")
        lines.append("CALLEES:")
        for r in callees:
            prefix = "  " * r["hop"]
            path = r.get("path")
            range_bit = ""
            if path:
                range_bit = f"  ({path}"
                if r.get("start_line") and r.get("end_line"):
                    range_bit += f":{r['start_line']}-{r['end_line']}"
                range_bit += ")"
            lines.append(
                f"{prefix}- [{r['confidence']:.2f}] {r['fqn']} (hop={r['hop']}){range_bit}"
            )
            if r.get("summary"):
                lines.append(f"{prefix}    {r['summary']}")
    if not callers and not callees:
        lines.append("  (no edges found)")
    return "\n".join(lines)


@cli.command(name="impact-of")
@click.option(
    "--config", required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the cell YAML; only target.dsn + target.database_name are read.",
)
@click.option(
    "--fqn", required=True,
    help="Fully-qualified name to find callers/callees for (e.g. pkg.module.func).",
)
@click.option(
    "--depth", type=int, default=1, show_default=True,
    help=f"Number of edge hops to walk. Hard-capped at {_IMPACT_MAX_DEPTH}.",
)
@click.option(
    "--direction",
    type=click.Choice(["callers", "callees", "both"]),
    default="callers", show_default=True,
    help="Walk callers (who calls X), callees (what X calls), or both.",
)
@click.option(
    "--edge-type", default="CALLS", show_default=True,
    help="Edge type to follow. Currently CALLS, INHERITS, IMPLEMENTS.",
)
@click.option(
    "--confidence", "confidence_floor",
    type=float, default=0.7, show_default=True,
    help="Minimum edge confidence (0.0-1.0). Ambiguous-name edges are 0.5.",
)
@click.option(
    "--project-id",
    default=None,
    help=(
        "project_id scope for the edges table. Defaults to the cell's "
        "cell_name (matching what the runner stamps)."
    ),
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit JSON instead of human-readable tree text.",
)
def impact_of(config, fqn, depth, direction, edge_type, confidence_floor, project_id, as_json):
    """Walk the code_edges table for callers/callees of a fully-qualified name.

    The cell YAML supplies the Postgres DSN and the schema; the impact query
    runs against ``<schema>.code_edges`` populated by the code_relationships
    extractor's finalize() pass.

    Examples:

    \b
        chunkshop impact-of --config cell.yaml --fqn pkg.module.func
        chunkshop impact-of --config cell.yaml --fqn pkg.module.Class \\
            --direction both --depth 2 --json
    """
    import json as _json

    import yaml as _yaml

    from chunkshop.config import CellConfig

    if depth < 1 or depth > _IMPACT_MAX_DEPTH:
        raise click.UsageError(
            f"--depth must be between 1 and {_IMPACT_MAX_DEPTH}, got {depth}"
        )

    try:
        cfg = CellConfig.model_validate(_yaml.safe_load(Path(config).read_text()))
        tgt = cfg.target
        if tgt.type != "postgres":
            raise click.UsageError(
                f"impact-of currently supports postgres targets only, got {tgt.type!r}"
            )

        pid = project_id or cfg.cell_name
        if not _IMPACT_PROJECT_RE.match(pid):
            raise click.UsageError(
                f"project_id must match ^[A-Za-z0-9_.\\-]+$, got {pid!r}"
            )

        dsn = tgt.resolve_dsn()
        schema = tgt.database_name
        table = tgt.table

        callers: list[dict] = []
        callees: list[dict] = []
        if direction in ("callers", "both"):
            callers = _impact_query_one_direction(
                dsn,
                schema=schema,
                fqn=fqn,
                direction="callers",
                depth=depth,
                project_id=pid,
                confidence_floor=confidence_floor,
                edge_type=edge_type,
            )
            callers = _enrich_with_chunk_metadata(
                dsn, schema=schema, table=table, rows=callers
            )
        if direction in ("callees", "both"):
            callees = _impact_query_one_direction(
                dsn,
                schema=schema,
                fqn=fqn,
                direction="callees",
                depth=depth,
                project_id=pid,
                confidence_floor=confidence_floor,
                edge_type=edge_type,
            )
            callees = _enrich_with_chunk_metadata(
                dsn, schema=schema, table=table, rows=callees
            )
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(str(exc))

    if as_json:
        out: dict = {
            "target": fqn,
            "project_id": pid,
            "depth": depth,
            "direction": direction,
            "edge_type": edge_type,
            "confidence_floor": confidence_floor,
        }
        if direction in ("callers", "both"):
            out["callers"] = callers
        if direction in ("callees", "both"):
            out["callees"] = callees
        click.echo(_json.dumps(out, indent=2, default=str))
    else:
        click.echo(_render_impact_text(fqn, callers, callees))


if __name__ == "__main__":
    cli()
