"""Output writers for bakeoff runs (multi-backend).

Three files land in `out_dir`:
- `results.json` — raw BakeoffResults, round-trips via pydantic.
- `report.md` — human-readable side-by-side leaderboard + per-backend
  leaderboards + per-query detail + statistical-power note.
- `recommended.yaml` — the top-MRR combo rendered as a runnable chunkshop
  `CellConfig`. Ties broken by preferring postgres for the recommended
  emit (other backends should match accuracy when chunks + vectors do).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from chunkshop.bakeoff.config import BakeoffConfig, BakeoffResults, ComboResult
from chunkshop.bakeoff.keys import chunker_key, embedder_key


_BACKEND_ORDER = ["postgres", "mariadb", "sqlite"]
_BACKEND_SHORT = {"postgres": "PG", "mariadb": "MD", "sqlite": "SQ"}


def write_results_json(results: BakeoffResults, out_dir: Path) -> Path:
    """Dump raw BakeoffResults to `{out_dir}/results.json`."""
    out = out_dir / "results.json"
    out.write_text(results.model_dump_json(indent=2))
    return out


def _backends_present(results: BakeoffResults) -> list[str]:
    """Return the list of backends that actually appear in `results.combos`,
    in the canonical _BACKEND_ORDER order (any extras appended)."""
    seen = {c.backend for c in results.combos}
    ordered = [b for b in _BACKEND_ORDER if b in seen]
    extras = sorted(seen - set(ordered))
    return ordered + extras


def _combos_for_backend(results: BakeoffResults, backend: str) -> list[ComboResult]:
    return [c for c in results.combos if c.backend == backend]


def _per_backend_leaderboard_lines(
    cfg: BakeoffConfig, combos: list[ComboResult], backend: str
) -> list[str]:
    if not combos:
        return [f"_(no combos for {backend})_", ""]
    ranked = sorted(combos, key=lambda c: -c.aggregate.get("mrr", 0))
    header_cols = " | ".join(f"r@{k}" for k in cfg.scoring.k)
    sep_cells = "|".join(["---"] * (len(cfg.scoring.k) + 8))
    lines = [
        f"| # | Chunker | Embedder | {header_cols} | MRR | chunks | ingest_s | embed_s | query_s |",
        f"|{sep_cells}|",
    ]
    for i, c in enumerate(ranked, start=1):
        rk = [f"{c.aggregate.get(f'recall_at_{k}', 0):.3f}" for k in cfg.scoring.k]
        mrr = f"{c.aggregate.get('mrr', 0):.3f}"
        lines.append(
            f"| {i} | `{c.chunker_label}` | `{c.embedder_label}` | "
            + " | ".join(rk)
            + f" | {mrr} | {c.ingest_chunks} | {c.ingest_wall_seconds:.2f} | "
            f"{c.ingest_embed_seconds:.2f} | {c.query_wall_seconds:.3f} |"
        )
    return lines


def _cross_backend_table_lines(
    results: BakeoffResults, backends: list[str]
) -> list[str]:
    """One row per (chunker, embedder) combo. Columns: per-backend MRR + ingest_s + query_s."""
    # Build (chunker_key, embedder_key) -> {backend: combo} index
    index: dict[tuple[str, str], dict[str, ComboResult]] = {}
    for c in results.combos:
        key = (c.chunker_key, c.embedder_key)
        index.setdefault(key, {})[c.backend] = c
    if not index:
        return ["_(no combos)_", ""]

    # Stable ordering: pick a canonical combo (any) for label; sort by best MRR across backends desc.
    def _best_mrr(by_backend: dict[str, ComboResult]) -> float:
        return max((c.aggregate.get("mrr", 0) for c in by_backend.values()), default=0)

    ordered_keys = sorted(
        index.keys(), key=lambda k: -_best_mrr(index[k])
    )

    short = [_BACKEND_SHORT.get(b, b[:2].upper()) for b in backends]
    header = (
        ["Chunker", "Embedder"]
        + [f"{s} MRR" for s in short]
        + [f"{s} ing_s" for s in short]
        + [f"{s} qry_s" for s in short]
    )
    sep = "|".join(["---"] * len(header))
    lines = [f"| {' | '.join(header)} |", f"|{sep}|"]
    for key in ordered_keys:
        by_b = index[key]
        sample = next(iter(by_b.values()))
        cells = [f"`{sample.chunker_label}`", f"`{sample.embedder_label}`"]
        for b in backends:
            c = by_b.get(b)
            cells.append(f"{c.aggregate.get('mrr', 0):.3f}" if c else "—")
        for b in backends:
            c = by_b.get(b)
            cells.append(f"{c.ingest_wall_seconds:.2f}" if c else "—")
        for b in backends:
            c = by_b.get(b)
            cells.append(f"{c.query_wall_seconds:.3f}" if c else "—")
        lines.append(f"| {' | '.join(cells)} |")
    return lines


def write_report_md(
    cfg: BakeoffConfig, results: BakeoffResults, out_dir: Path
) -> Path:
    """Render side-by-side multi-backend report."""
    backends = _backends_present(results)
    n_backends = max(len(backends), 1)
    combos_per_backend = results.n_combos // n_backends

    lines: list[str] = [
        f"# Bakeoff report: {results.run_name} (multi-backend)",
        "",
        f"- Run: {results.started_at}",
        f"- Corpus: {results.corpus_label}",
        f"- Queries: {results.n_queries}",
        f"- Backends: {', '.join(backends) if backends else '(none)'}",
        f"- Combos per backend: {combos_per_backend}",
        f"- Total cells: {results.n_combos}",
        "",
        "## Cross-backend comparison",
        "",
        "For each (chunker, embedder), MRR + ingest/query wall time per backend. "
        "Chunks and vectors are identical across backends, so MRR should match; "
        "differences in ingest_s and query_s reflect the backend itself.",
        "",
    ]
    lines += _cross_backend_table_lines(results, backends)

    # Per-backend leaderboards
    for b in backends:
        combos = _combos_for_backend(results, b)
        lines += [
            "",
            f"## {b.capitalize()} leaderboard",
            "",
        ]
        lines += _per_backend_leaderboard_lines(cfg, combos, b)

    # Per-query detail across backends
    lines += [
        "",
        "## Per-query detail (top-1 hit per backend × combo)",
        "",
        "| Backend | Chunker | Embedder | Query | Gold | Top-1 | MRR |",
        "|---|---|---|---|---|---|---|",
    ]
    # Same ordering as the per-backend leaderboard: best MRR first within backend.
    for b in backends:
        combos = sorted(
            _combos_for_backend(results, b),
            key=lambda c: -c.aggregate.get("mrr", 0),
        )
        for c in combos:
            for pq in c.per_query:
                top1 = pq["top_k"][0]["doc_id"] if pq.get("top_k") else "-"
                lines.append(
                    f"| {b} | `{c.chunker_label}` | `{c.embedder_label}` | "
                    f"{pq['query']} | `{pq['gold_doc_id']}` | "
                    f"`{top1}` | {pq.get('mrr', 0):.3f} |"
                )

    # Query-time embedding cost (per-embedder, unchanged shape)
    if results.query_embed_seconds_by_embedder:
        n = max(results.n_queries, 1)
        lines += [
            "",
            "## Query-time embedding cost",
            "",
            f"Wall time to embed all {results.n_queries} gold queries, per "
            "unique embedder. At production scale this scales by your "
            "expected QPS — useful for choosing between a slower-but-better "
            "embedder and a faster-but-worse one.",
            "",
            "| Embedder | total_s | per_query_ms |",
            "|---|---|---|",
        ]
        for k, total in sorted(
            results.query_embed_seconds_by_embedder.items(), key=lambda kv: kv[1]
        ):
            per_q_ms = (total / n) * 1000.0
            lines.append(f"| `{k}` | {total:.3f} | {per_q_ms:.1f} |")

    # Honesty note scaled to n_queries.
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


def _pick_recommended(results: BakeoffResults) -> ComboResult:
    """Top MRR combo, ties broken by preferring postgres > mariadb > sqlite."""
    backend_pref = {b: i for i, b in enumerate(_BACKEND_ORDER)}

    def sort_key(c: ComboResult):
        return (
            -c.aggregate.get("mrr", 0),
            backend_pref.get(c.backend, 99),
            c.chunker_key,
            c.embedder_key,
        )

    return sorted(results.combos, key=sort_key)[0]


def write_recommended_yaml(
    cfg: BakeoffConfig, results: BakeoffResults, out_dir: Path
) -> Path:
    """Render the top-MRR combo as a runnable CellConfig YAML.

    Multi-backend run: ties broken by backend preference (postgres > mariadb >
    sqlite). The recommended target inherits the winner's backend type; the
    user can swap it after copying the file.
    """
    top = _pick_recommended(results)

    winner_chunker = next(
        c for c in cfg.matrix.chunkers if chunker_key(c) == top.chunker_key
    )
    winner_embedder = next(
        e for e in cfg.matrix.embedders if embedder_key(e) == top.embedder_key
    )
    winner_target = next(
        t for t in cfg.targets if t.type == top.backend
    )

    recommended = {
        "# NOTE": (
            f"Top combo from multi-backend bakeoff '{results.run_name}' "
            f"(backend={top.backend}, MRR={top.aggregate.get('mrr', 0):.3f}, "
            f"r@1={top.aggregate.get('recall_at_1', 0):.3f}). "
            "Point `source` at your real corpus before running `chunkshop ingest`."
        ),
        "cell_name": f"{results.run_name}_recommended",
        "source": cfg.source.model_dump(exclude_none=True, by_alias=True),
        "framer": cfg.framer.model_dump(exclude_none=True, by_alias=True),
        "chunker": winner_chunker.model_dump(exclude_none=True, by_alias=True),
        "embedder": winner_embedder.model_dump(exclude_none=True, by_alias=True),
        "target": {
            "type": top.backend,
            "dsn_env": winner_target.dsn_env,
            "database": winner_target.database_name,
            "table": f"{results.run_name}_production",
            "mode": "overwrite",
        },
    }
    out = out_dir / "recommended.yaml"
    out.write_text(yaml.safe_dump(recommended, sort_keys=False))
    return out
