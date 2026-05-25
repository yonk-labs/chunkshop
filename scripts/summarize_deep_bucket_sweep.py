#!/usr/bin/env python3
"""Summarize a Chunkshop deep bucket sweep after llm-judge runs complete.

This is intentionally a reporting-only script. It never calls providers and can
be rerun safely while a long sweep is still in progress; missing workloads are
reported as pending.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def status(row: dict[str, Any]) -> str:
    verdict = str(row.get("verdict") or "").upper()
    if row.get("error") or verdict == "ERROR":
        return "ERROR"
    if verdict:
        return verdict
    return "PASSED" if row.get("passed") else "UNKNOWN"


def settings(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("settings")
    return value if isinstance(value, dict) else {}


def workload(row: dict[str, Any]) -> str:
    s = settings(row)
    return str(s.get("workload") or s.get("dataset") or row.get("question_class") or "unknown")


def bucket(row: dict[str, Any]) -> str:
    s = settings(row)
    return str(s.get("bucket") or s.get("dataset") or row.get("question_class") or "unknown")


def config_label(row: dict[str, Any]) -> str:
    s = settings(row)
    return str(s.get("config_label") or row.get("config_label") or "unknown")


def token_counts(row: dict[str, Any]) -> dict[str, Any]:
    tc = settings(row).get("token_counts")
    return tc if isinstance(tc, dict) else {}


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {
            "n": 0,
            "passed": 0,
            "errors": 0,
            "accuracy": 0.0,
            "score": 0.0,
            "latency_ms": 0.0,
            "context_tokens": 0.0,
            "token_reduction": 0.0,
        }
    errors = sum(1 for r in rows if status(r) == "ERROR")
    passed = sum(1 for r in rows if bool(r.get("passed")) and status(r) != "ERROR")
    scores = [float(r.get("score") or 0.0) for r in rows if status(r) != "ERROR"]
    latencies = [float(r.get("latency_ms") or 0.0) for r in rows if status(r) != "ERROR"]
    tokens = []
    reductions = []
    oracle_reductions = []
    for row in rows:
        tc = token_counts(row)
        if "context_tokens_est" in tc:
            tokens.append(float(tc["context_tokens_est"]))
        if "token_reduction_vs_raw_context" in tc:
            reductions.append(float(tc["token_reduction_vs_raw_context"]))
        if "token_reduction_vs_oracle" in tc:
            oracle_reductions.append(float(tc["token_reduction_vs_oracle"]))
    return {
        "n": n,
        "passed": passed,
        "errors": errors,
        "accuracy": passed / n,
        "score": mean(scores),
        "latency_ms": mean(latencies),
        "context_tokens": mean(tokens),
        "token_reduction": mean(reductions),
        "oracle_token_reduction": mean(oracle_reductions),
    }


def grouped(rows: list[dict[str, Any]], keys: list[str]) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    out: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        vals = []
        for key in keys:
            if key == "workload":
                vals.append(workload(row))
            elif key == "bucket":
                vals.append(bucket(row))
            elif key == "config":
                vals.append(config_label(row))
            else:
                vals.append(str(settings(row).get(key) or "unknown"))
        out[tuple(vals)].append(row)
    return dict(out)


def sort_configs(items: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(
        items,
        key=lambda item: (
            item[1]["errors"] == 0,
            item[1]["accuracy"],
            item[1]["score"],
            -item[1]["context_tokens"],
        ),
        reverse=True,
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "workload",
        "bucket",
        "config",
        "cases",
        "passed",
        "errors",
        "accuracy",
        "score",
        "avg_latency_ms",
        "avg_context_tokens",
        "avg_token_reduction",
        "avg_oracle_token_reduction",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def csv_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for (w, b, c), group in sorted(grouped(rows, ["workload", "bucket", "config"]).items()):
        s = stats(group)
        out.append(
            {
                "workload": w,
                "bucket": b,
                "config": c,
                "cases": s["n"],
                "passed": s["passed"],
                "errors": s["errors"],
                "accuracy": f"{s['accuracy']:.4f}",
                "score": f"{s['score']:.4f}",
                "avg_latency_ms": f"{s['latency_ms']:.1f}",
                "avg_context_tokens": f"{s['context_tokens']:.1f}",
                "avg_token_reduction": f"{s['token_reduction']:.4f}",
                "avg_oracle_token_reduction": f"{s['oracle_token_reduction']:.4f}",
            }
        )
    return out


def render_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def write_summary(out_dir: Path, rows: list[dict[str, Any]], pending: list[str]) -> None:
    report = out_dir / "reports/deep-bucket-combined-summary.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    all_stats = stats(rows)
    lines = [
        "# Deep Bucket Sweep Combined Summary",
        "",
        f"- Generated: `{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}`",
        f"- Cases loaded: `{all_stats['n']}`",
        f"- Passed: `{all_stats['passed']}`",
        f"- Errors: `{all_stats['errors']}`",
        f"- Pass rate: `{all_stats['accuracy']:.1%}`",
        f"- Average score: `{all_stats['score']:.3f}`",
        "",
    ]
    if pending:
        lines += ["## Pending Workloads", ""]
        lines += [f"- `{name}`" for name in pending]
        lines += [""]
    if all_stats["errors"]:
        lines += [
            "## Validity Warning",
            "",
            "This run contains provider `ERROR` rows. Do not publish accuracy claims "
            "until the errors are reviewed or the failed rows are rerun.",
            "",
        ]

    lines += ["## Workloads", ""]
    workload_rows = []
    for (w,), group in sorted(grouped(rows, ["workload"]).items()):
        s = stats(group)
        workload_rows.append(
            [
                f"`{w}`",
                str(s["n"]),
                f"{s['accuracy']:.1%}",
                f"{s['score']:.3f}",
                str(s["errors"]),
                f"{s['context_tokens']:.0f}",
                f"{s['token_reduction']:.1%}",
            ]
        )
    lines += render_table(
        ["Workload", "Cases", "Pass", "Score", "Errors", "Avg ctx tok", "Avg reduction"],
        workload_rows,
    )

    lines += ["", "## Top Configs by Workload", ""]
    for (w,), group in sorted(grouped(rows, ["workload"]).items()):
        config_stats = [(c, stats(g)) for (c,), g in grouped(group, ["config"]).items()]
        lines += [f"### `{w}`", ""]
        table_rows = []
        for config, s in sort_configs(config_stats)[:20]:
            table_rows.append(
                [
                    f"`{config}`",
                    str(s["n"]),
                    f"{s['accuracy']:.1%}",
                    f"{s['score']:.3f}",
                    str(s["errors"]),
                    f"{s['context_tokens']:.0f}",
                    f"{s['token_reduction']:.1%}",
                ]
            )
        lines += render_table(
            ["Config", "Cases", "Pass", "Score", "Errors", "Avg ctx tok", "Avg reduction"],
            table_rows,
        )
        lines += [""]

    lines += [
        "## Artifacts",
        "",
        "- `reports/deep-bucket-combined.csv`",
        "- `reports/profile_recommendations.md`",
        "- `reports/profile_calibration.json`",
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")


def write_recommendations(out_dir: Path, rows: list[dict[str, Any]], pending: list[str]) -> None:
    path = out_dir / "reports/profile_recommendations.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Profile Recommendations",
        "",
        "These recommendations are generated from completed `llm-judge` rows. "
        "They are advisory until every intended workload has finished with an acceptable error count.",
        "",
    ]
    if pending:
        lines += ["## Pending", ""]
        lines += [f"- `{name}`" for name in pending]
        lines += [""]

    for (w,), group in sorted(grouped(rows, ["workload"]).items()):
        config_stats = sort_configs([(c, stats(g)) for (c,), g in grouped(group, ["config"]).items()])
        if not config_stats:
            continue
        best = config_stats[0]
        low_cost = min(config_stats, key=lambda item: (-item[1]["accuracy"], item[1]["context_tokens"]))
        token_frugal = min(config_stats, key=lambda item: (item[1]["context_tokens"], -item[1]["accuracy"]))
        lines += [
            f"## `{w}`",
            "",
            f"- Best current config: `{best[0]}` "
            f"({best[1]['accuracy']:.1%} pass, score {best[1]['score']:.3f}, "
            f"{best[1]['context_tokens']:.0f} avg context tokens).",
            f"- Accuracy-first/low-token tie-break: `{low_cost[0]}` "
            f"({low_cost[1]['accuracy']:.1%}, {low_cost[1]['context_tokens']:.0f} tokens).",
            f"- Cheapest observed config: `{token_frugal[0]}` "
            f"({token_frugal[1]['accuracy']:.1%}, {token_frugal[1]['context_tokens']:.0f} tokens).",
            "",
        ]

    lines += [
        "## Interpretation Rules",
        "",
        "- Prefer per-workload recommendations over aggregate rankings.",
        "- Do not pick a profile with provider errors until those rows are rerun.",
        "- Treat summary-only winners cautiously on cross-document workloads.",
        "- Keep `raw` as an escape hatch even when compact profiles win.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_calibration(out_dir: Path, rows: list[dict[str, Any]], source_runs: dict[str, Path], pending: list[str]) -> None:
    path = out_dir / "reports/profile_calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    workloads: dict[str, Any] = {}
    configs: dict[str, Any] = {}
    for (w,), group in grouped(rows, ["workload"]).items():
        workloads[w] = stats(group)
    for (c,), group in grouped(rows, ["config"]).items():
        configs[c] = stats(group)
    by_workload_config: dict[str, Any] = {}
    for (w, c), group in grouped(rows, ["workload", "config"]).items():
        by_workload_config.setdefault(w, {})[c] = stats(group)
    payload = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_runs": {name: str(path) for name, path in source_runs.items()},
        "pending_workloads": pending,
        "aggregate": stats(rows),
        "workloads": workloads,
        "configs": configs,
        "by_workload_config": by_workload_config,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_runs(out_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Path], list[str]]:
    expected = {
        "scotus_50_buckets": out_dir / "llm-judge-runs/scotus-50-matrix/results.jsonl",
        "longbench": out_dir / "llm-judge-runs/longbench-matrix/results.jsonl",
    }
    rows: list[dict[str, Any]] = []
    source_runs: dict[str, Path] = {}
    pending: list[str] = []
    for name, path in expected.items():
        run_rows = read_jsonl(path)
        if run_rows:
            source_runs[name] = path
            rows.extend(run_rows)
        else:
            pending.append(name)
    return rows, source_runs, pending


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", type=Path, help="Deep sweep output directory")
    args = ap.parse_args()
    out_dir = args.out_dir.resolve()
    rows, source_runs, pending = load_runs(out_dir)
    if not rows:
        raise SystemExit(f"no completed llm-judge result rows found under {out_dir}")
    write_csv(out_dir / "reports/deep-bucket-combined.csv", csv_rows(rows))
    write_summary(out_dir, rows, pending)
    write_recommendations(out_dir, rows, pending)
    write_calibration(out_dir, rows, source_runs, pending)
    print(f"wrote reports under {out_dir / 'reports'}")
    if pending:
        print("pending workloads:", ", ".join(pending))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
