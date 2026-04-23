"""Output writers for bakeoff runs (SC-006, SC-007, SC-008).

Three files land in `out_dir`:
- `results.json` — raw BakeoffResults, round-trips via pydantic.
- `report.md` — human-readable leaderboard + statistical-power honesty note.
- `recommended.yaml` — the top-MRR combo rendered as a runnable chunkshop
  `CellConfig`. User points `source` at their real corpus and runs `chunkshop
  ingest` with it.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from chunkshop.bakeoff.config import BakeoffConfig, BakeoffResults
from chunkshop.bakeoff.keys import chunker_key, embedder_key


def write_results_json(results: BakeoffResults, out_dir: Path) -> Path:
    """Dump raw BakeoffResults to `{out_dir}/results.json`."""
    out = out_dir / "results.json"
    out.write_text(results.model_dump_json(indent=2))
    return out


def write_report_md(
    cfg: BakeoffConfig, results: BakeoffResults, out_dir: Path
) -> Path:
    """Render a markdown leaderboard sorted by MRR desc + honesty note."""
    ranked = sorted(results.combos, key=lambda c: -c.aggregate.get("mrr", 0))

    header_cols = " | ".join(f"r@{k}" for k in cfg.scoring.k)
    sep_cells = "|".join(["---"] * (len(cfg.scoring.k) + 4))

    lines = [
        f"# Bakeoff report: {results.run_name}",
        "",
        f"- Run: {results.started_at}",
        f"- Corpus: {results.corpus_label}",
        f"- Queries: {results.n_queries}",
        f"- Combos: {results.n_combos}",
        "",
        "## Leaderboard (sorted by MRR)",
        "",
        f"| # | Chunker | Embedder | {header_cols} | MRR |",
        f"|{sep_cells}|",
    ]
    for i, c in enumerate(ranked, start=1):
        rk = [f"{c.aggregate.get(f'recall_at_{k}', 0):.3f}" for k in cfg.scoring.k]
        mrr = f"{c.aggregate.get('mrr', 0):.3f}"
        lines.append(
            f"| {i} | `{c.chunker_label}` | `{c.embedder_label}` | "
            + " | ".join(rk)
            + f" | {mrr} |"
        )

    # Per-query detail: top-1 doc_id per combo, one row per (combo, query).
    lines += [
        "",
        "## Per-query detail (top-1 hit per combo)",
        "",
        "| Chunker | Embedder | Query | Gold | Top-1 | MRR |",
        "|---|---|---|---|---|---|",
    ]
    for c in ranked:
        for pq in c.per_query:
            top1 = pq["top_k"][0]["doc_id"] if pq.get("top_k") else "-"
            lines.append(
                f"| `{c.chunker_label}` | `{c.embedder_label}` | "
                f"{pq['query']} | `{pq['gold_doc_id']}` | "
                f"`{top1}` | {pq.get('mrr', 0):.3f} |"
            )

    # Honesty note scaled to n_queries. Load-bearing — never drop this.
    n = max(results.n_queries, 1)
    lines += [
        "",
        "## Statistical power",
        "",
        f"{results.n_queries} queries means one query flipping moves aggregate recall by "
        f"{1 / n:.3f}. Combos within ~{2 / n:.2f} of the leader are not reliably "
        "distinguishable. Re-run with more queries or a larger corpus before treating "
        "the leaderboard as a tournament result.",
        "",
    ]
    out = out_dir / "report.md"
    out.write_text("\n".join(lines))
    return out


def write_recommended_yaml(
    cfg: BakeoffConfig, results: BakeoffResults, out_dir: Path
) -> Path:
    """Render the top-MRR combo as a runnable CellConfig YAML.

    Round-trip through `CellConfig.model_validate` is covered by
    test_bakeoff_output::test_recommended_yaml_parses_as_cell_config.
    """
    ranked = sorted(results.combos, key=lambda c: -c.aggregate.get("mrr", 0))
    top = ranked[0]

    winner_chunker = next(
        c for c in cfg.matrix.chunkers if chunker_key(c) == top.chunker_key
    )
    winner_embedder = next(
        e for e in cfg.matrix.embedders if embedder_key(e) == top.embedder_key
    )

    # Use pydantic's `by_alias=True` so `schema_name` dumps as `schema`
    # (matches the YAML/chunkshop convention and round-trips through
    # TargetConfig.model_validate).
    recommended = {
        "# NOTE": (
            f"Top combo from bakeoff '{results.run_name}' "
            f"(MRR={top.aggregate.get('mrr', 0):.3f}, "
            f"r@1={top.aggregate.get('recall_at_1', 0):.3f}). "
            "Point `source` at your real corpus before running `chunkshop ingest`."
        ),
        "cell_name": f"{results.run_name}_recommended",
        "source": cfg.source.model_dump(exclude_none=True, by_alias=True),
        "framer": cfg.framer.model_dump(exclude_none=True, by_alias=True),
        "chunker": winner_chunker.model_dump(exclude_none=True, by_alias=True),
        "embedder": winner_embedder.model_dump(exclude_none=True, by_alias=True),
        "target": {
            "dsn_env": cfg.target.dsn_env,
            "schema": cfg.target.schema_name,
            "table": f"{results.run_name}_production",
            "mode": "overwrite",
        },
    }
    out = out_dir / "recommended.yaml"
    out.write_text(yaml.safe_dump(recommended, sort_keys=False))
    return out
