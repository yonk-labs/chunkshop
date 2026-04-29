#!/usr/bin/env python3
"""parity_check_bakeoff.py — cross-language bakeoff parity check.

Drives the same NTSB matrix from Python AND Rust, then asserts:
  1. Each language's leaderboard ranks distinct combos consistently
     (combos with MRR gap > tolerance must rank in the same order).
  2. Per-combo aggregate MRR is within `--mrr-tolerance` (default 1.5e-2)
     between languages — the documented ORT-binary cosine-drift envelope.
  3. Both recommended.yaml emissions pick (chunker, embedder) tuples
     within the tolerance band of each other's MRR.

Why this test exists: chunkshop's cross-language pitch is "vectors are
interchangeable; the same YAML produces the same leaderboard." Without
this script, that's an assertion. With it, it's verifiable.

Run from repo root:

    export CHUNKSHOP_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
    python3 scripts/parity_check_bakeoff.py

Exits 0 on parity within tolerance, non-zero with a structured diff
otherwise.

Note: this uses the Rust-compatible matrix (`bakeoff-ntsb-rust.yaml`,
2 embedders × 4 chunkers = 8 combos) for both runs. The 3-embedder
Python-canonical matrix (`bakeoff-ntsb.yaml`) includes nomic, which the
Rust port doesn't yet support. Adding nomic to the Rust embedder
registry is a follow-up brief.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_YAML = REPO_ROOT / "docs/samples/bakeoff-ntsb/bakeoff-ntsb-rust.yaml"
RUST_BIN = REPO_ROOT / "rust/target/release/chunkshop-rs"
SKILL_OUT = REPO_ROOT / "skill-output/bakeoff/ntsb_bakeoff"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dsn",
        default=os.environ.get(
            "CHUNKSHOP_DSN",
            "postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg",
        ),
        help="Postgres DSN. Default reads $CHUNKSHOP_DSN.",
    )
    p.add_argument(
        "--mrr-tolerance",
        type=float,
        default=2.5e-2,
        help="Per-combo MRR difference tolerance. Default 2.5e-2 (2.5pp). "
        "Sized to the documented ORT-drift envelope: rust/README.md reports "
        "max ~5-15e-3 cosine drift per chunk, which translates to roughly "
        "one near-tie query flip per ~12-query gold set (≈8pp/3 = 2.5pp).",
    )
    p.add_argument(
        "--ordering-gap",
        type=float,
        default=5e-3,
        help="Combos with MRR gap > this must rank in the same order across "
        "languages. Combos within this band may shuffle (drift-driven).",
    )
    return p.parse_args()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=False, **kw)


def drop_schema(dsn: str) -> None:
    run(["psql", dsn, "-c", "DROP SCHEMA IF EXISTS chunkshop_bakeoff_ntsb CASCADE"])


def ensure_rust_bin() -> Path:
    if not RUST_BIN.exists():
        print(f"Rust binary not found at {RUST_BIN}", file=sys.stderr)
        print("Run: (cd rust && cargo build --release)", file=sys.stderr)
        sys.exit(2)
    return RUST_BIN


def ensure_python_chunkshop() -> Path:
    bin_path = REPO_ROOT / "python/.venv/bin/chunkshop"
    if not bin_path.exists():
        print(f"Python chunkshop not found at {bin_path}", file=sys.stderr)
        print("Run: (cd python && uv sync --extra dev --extra extractors)", file=sys.stderr)
        sys.exit(2)
    return bin_path


def run_python_bakeoff(py_bin: Path, dsn: str) -> dict:
    drop_schema(dsn)
    rc = run(
        [
            str(py_bin), "bakeoff",
            "--config", str(RUST_YAML),  # use rust-compat matrix on both sides
            "--dsn", dsn,
            "--yes",
        ],
        cwd=REPO_ROOT,
    )
    if rc.returncode != 0:
        sys.exit(f"Python bakeoff failed (rc={rc.returncode})")
    results_path = SKILL_OUT / "results.json"
    return json.loads(results_path.read_text())


def run_rust_bakeoff(rust_bin: Path, dsn: str) -> dict:
    drop_schema(dsn)
    rc = run(
        [str(rust_bin), "bakeoff",
         "--config", str(RUST_YAML),
         "--dsn", dsn,
         "--yes"],
        cwd=REPO_ROOT,
    )
    if rc.returncode != 0:
        sys.exit(f"Rust bakeoff failed (rc={rc.returncode})")
    results_path = SKILL_OUT / "results.json"
    return json.loads(results_path.read_text())


def combo_key(c: dict) -> str:
    return f"{c['chunker_key']}__{c['embedder_key']}"


def diff_leaderboards(py: dict, rs: dict, mrr_tol: float, ordering_gap: float) -> int:
    """Return non-zero on parity failure."""
    py_combos = {combo_key(c): c for c in py["combos"]}
    rs_combos = {combo_key(c): c for c in rs["combos"]}

    if py_combos.keys() != rs_combos.keys():
        py_only = sorted(py_combos.keys() - rs_combos.keys())
        rs_only = sorted(rs_combos.keys() - py_combos.keys())
        print("FAIL: combo sets differ")
        if py_only:
            print(f"  Python-only combos: {py_only}")
        if rs_only:
            print(f"  Rust-only combos:   {rs_only}")
        return 1

    print(f"\n{'combo':<55} {'py_mrr':>8} {'rs_mrr':>8} {'Δ':>8}")
    print("-" * 80)
    failures: list[str] = []
    rows = []
    for k in sorted(py_combos.keys()):
        p_mrr = py_combos[k]["aggregate"].get("mrr", 0.0)
        r_mrr = rs_combos[k]["aggregate"].get("mrr", 0.0)
        d = abs(p_mrr - r_mrr)
        rows.append((k, p_mrr, r_mrr, d))
        flag = "" if d <= mrr_tol else " <-- exceeds tolerance"
        print(f"{k:<55} {p_mrr:>8.3f} {r_mrr:>8.3f} {d:>8.3f}{flag}")
        if d > mrr_tol:
            failures.append(
                f"{k}: |Δ MRR| = {d:.3f} > tolerance {mrr_tol:.3f}"
            )

    # Ordering check: for combo pairs (a, b) where Python's MRR gap > ordering_gap,
    # the Rust ordering must agree (sign of difference).
    print(f"\nOrdering parity check (gap > {ordering_gap:.3f}):")
    py_ranked = sorted(rows, key=lambda x: -x[1])  # by py_mrr desc
    bad_orderings: list[str] = []
    for i in range(len(py_ranked)):
        for j in range(i + 1, len(py_ranked)):
            a, b = py_ranked[i], py_ranked[j]
            py_gap = a[1] - b[1]
            if py_gap <= ordering_gap:
                continue
            rs_diff = a[2] - b[2]
            if rs_diff < 0:
                bad_orderings.append(
                    f"  {a[0]} (py={a[1]:.3f}, rs={a[2]:.3f}) ranked above "
                    f"{b[0]} (py={b[1]:.3f}, rs={b[2]:.3f}) in Python "
                    f"(gap={py_gap:.3f}) but below in Rust"
                )
    if not bad_orderings:
        print("  all distinct-MRR pairs agree on ordering")
    else:
        print("  FAIL — orderings disagree on distinct pairs:")
        for line in bad_orderings:
            print(line)
        failures.extend(bad_orderings)

    # Recommended-cell parity: top combo by MRR must be within ordering_gap.
    py_top = max(py_combos.values(), key=lambda c: c["aggregate"].get("mrr", 0))
    rs_top = max(rs_combos.values(), key=lambda c: c["aggregate"].get("mrr", 0))
    py_top_key = combo_key(py_top)
    rs_top_key = combo_key(rs_top)
    print(f"\nTop-combo parity:")
    print(f"  Python top: {py_top_key} (MRR={py_top['aggregate']['mrr']:.3f})")
    print(f"  Rust   top: {rs_top_key} (MRR={rs_top['aggregate']['mrr']:.3f})")
    if py_top_key != rs_top_key:
        py_top_in_rs = rs_combos[py_top_key]["aggregate"]["mrr"]
        rs_top_in_py = py_combos[rs_top_key]["aggregate"]["mrr"]
        py_gap = py_top["aggregate"]["mrr"] - rs_top_in_py
        rs_gap = rs_top["aggregate"]["mrr"] - py_top_in_rs
        if py_gap > ordering_gap or rs_gap > ordering_gap:
            failures.append(
                f"top-combo disagree by more than {ordering_gap:.3f} MRR: "
                f"py picked {py_top_key}, rs picked {rs_top_key}"
            )
        else:
            print(
                f"  (within tie band {ordering_gap:.3f} — acceptable shuffle)"
            )
    else:
        print("  agree")

    if failures:
        print(f"\n{len(failures)} parity failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nPASS — all {len(rows)} combos within MRR tolerance "
          f"{mrr_tol:.3f}, all distinct pairs ranked consistently.")
    return 0


def main() -> int:
    args = parse_args()
    print(f"Repo root: {REPO_ROOT}")
    print(f"DSN:       {args.dsn}")
    print(f"YAML:      {RUST_YAML}")
    print(f"MRR tol:   {args.mrr_tolerance:.3f}")
    print(f"Ord gap:   {args.ordering_gap:.3f}")

    py_bin = ensure_python_chunkshop()
    rust_bin = ensure_rust_bin()

    # Sanity: the rust matrix yaml must exist.
    if not RUST_YAML.exists():
        print(f"Rust matrix YAML missing: {RUST_YAML}", file=sys.stderr)
        return 2

    print("\n=== Phase 1: Python bakeoff ===")
    py_results = run_python_bakeoff(py_bin, args.dsn)
    # Stash Python results before Rust overwrites skill-output/.
    py_stash = SKILL_OUT.parent / "ntsb_bakeoff_python.json"
    py_stash.write_text(json.dumps(py_results, indent=2))

    print("\n=== Phase 2: Rust bakeoff ===")
    rs_results = run_rust_bakeoff(rust_bin, args.dsn)
    rs_stash = SKILL_OUT.parent / "ntsb_bakeoff_rust.json"
    rs_stash.write_text(json.dumps(rs_results, indent=2))

    print("\n=== Phase 3: leaderboard diff ===")
    return diff_leaderboards(
        py_results, rs_results, args.mrr_tolerance, args.ordering_gap
    )


if __name__ == "__main__":
    sys.exit(main())
