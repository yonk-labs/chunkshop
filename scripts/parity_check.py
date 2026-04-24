#!/usr/bin/env python3
"""Cross-language parity check for chunkshop Python vs Rust implementations.

Runs both `chunkshop ingest` (Python) and `chunkshop-rs ingest` (Rust) against
the same corpus, into two tables in the same database, then compares:
  - chunk count, per-doc breakdown
  - chunk text identity (expected: byte-for-byte on prose)
  - top-k retrieval overlap for a fixed query
  - cosine distance between matched chunks

Writes a report to `skill-output/rust-parity/report.md`. This is NOT a pytest —
it requires both the Python and Rust toolchains installed and is run manually.

Usage:
    export CHUNKSHOP_DSN="postgresql://..."
    cd <repo-root>
    python scripts/parity_check.py \\
        --corpus "docs/samples/*-*.md" \\
        --rust-bin rust/target/release/chunkshop-rs

Known drift: fastembed-rs's BGEBaseENV15Q maps to Qdrant's fp32-optimized ONNX,
while Python uses Xenova's int8-quantized ONNX. Cosine similarity is typically
~0.99 (distance ~0.01) — compatible wire format, but not bit-exact.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    print(f"[parity] {msg}", flush=True)


def write_config(
    out_path: Path,
    *,
    cell_name: str,
    corpus_glob: str,
    dsn_env: str,
    schema: str,
    table: str,
    include_extractor: bool = True,
) -> None:
    extractor_block = "extractor:\n  type: none\n\n" if include_extractor else ""
    body = (
        f"cell_name: {cell_name}\n"
        "source:\n"
        "  type: files\n"
        f"  glob: {corpus_glob}\n"
        "  id_from: stem\n"
        "  encoding: utf-8\n"
        "\n"
        "chunker:\n"
        "  type: sentence_aware\n"
        "  max_chars: 2000\n"
        "  min_chars: 0\n"
        "\n"
        "embedder:\n"
        "  type: fastembed\n"
        "  model_name: Xenova/bge-base-en-v1.5-int8\n"
        "  dim: 768\n"
        "  batch_size: 8\n"
        "\n"
        f"{extractor_block}"
        "target:\n"
        f"  dsn_env: {dsn_env}\n"
        f"  schema: {schema}\n"
        f"  table: {table}\n"
        "  mode: overwrite\n"
        "  hnsw: false\n"
    )
    out_path.write_text(body)


def run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="docs/samples/*-*.md")
    ap.add_argument("--dsn-env", default="CHUNKSHOP_DSN")
    ap.add_argument(
        "--rust-bin",
        default="rust/target/release/chunkshop-rs",
        help="Path to the compiled chunkshop-rs binary",
    )
    ap.add_argument(
        "--query",
        default="what conventions does engineering follow",
        help="Fixed query for top-k retrieval comparison",
    )
    ap.add_argument(
        "--topk",
        type=int,
        default=5,
    )
    ap.add_argument("--py-schema", default="chunkshop_parity_py")
    ap.add_argument("--rs-schema", default="chunkshop_parity_rs")
    ap.add_argument("--table", default="chunks")
    args = ap.parse_args()

    if args.dsn_env not in os.environ:
        log(f"ERROR: env var {args.dsn_env} is not set")
        return 2
    dsn = os.environ[args.dsn_env]

    corpus_glob = str((REPO_ROOT / args.corpus).resolve()) if not os.path.isabs(args.corpus) else args.corpus
    rust_bin = Path(args.rust_bin)
    if not rust_bin.is_absolute():
        rust_bin = (REPO_ROOT / rust_bin).resolve()
    if not rust_bin.exists():
        log(f"ERROR: rust binary not found at {rust_bin}. Build with `cd rust && cargo build --release`.")
        return 2

    work = Path(tempfile.mkdtemp(prefix="chunkshop-parity-"))
    py_cfg = work / "py.yaml"
    rs_cfg = work / "rs.yaml"
    write_config(
        py_cfg,
        cell_name="parity_py",
        corpus_glob=corpus_glob,
        dsn_env=args.dsn_env,
        schema=args.py_schema,
        table=args.table,
        include_extractor=True,
    )
    write_config(
        rs_cfg,
        cell_name="parity_rs",
        corpus_glob=corpus_glob,
        dsn_env=args.dsn_env,
        schema=args.rs_schema,
        table=args.table,
        include_extractor=False,
    )

    log(f"Python ingest -> {args.py_schema}.{args.table}")
    run_cmd(
        ["uv", "run", "chunkshop", "ingest", "--config", str(py_cfg)],
        cwd=REPO_ROOT / "python",
    )

    log(f"Rust ingest -> {args.rs_schema}.{args.table}")
    run_cmd([str(rust_bin), "ingest", "--config", str(rs_cfg)])

    # Embed the query once (using Python's fastembed, matching the ingest model).
    log("embedding parity query with Python fastembed")
    from fastembed import TextEmbedding  # type: ignore

    # Register int8 variants if not already
    from chunkshop.embedders._registry import register_int8_variants  # type: ignore

    register_int8_variants()
    model = TextEmbedding(model_name="Xenova/bge-base-en-v1.5-int8")
    q_vec = list(next(iter(model.embed([args.query]))))
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in q_vec) + "]"

    import psycopg  # type: ignore

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # Counts
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT doc_id) FROM {args.py_schema}.{args.table}")
        py_rows, py_docs = cur.fetchone()
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT doc_id) FROM {args.rs_schema}.{args.table}")
        rs_rows, rs_docs = cur.fetchone()

        # Top-k by cosine
        cur.execute(
            f"SELECT id, doc_id FROM {args.py_schema}.{args.table} "
            f"ORDER BY embedding <=> %s::vector LIMIT %s",
            (vec_literal, args.topk),
        )
        py_top = cur.fetchall()
        cur.execute(
            f"SELECT id, doc_id FROM {args.rs_schema}.{args.table} "
            f"ORDER BY embedding <=> %s::vector LIMIT %s",
            (vec_literal, args.topk),
        )
        rs_top = cur.fetchall()

        # Matched-chunk cosine distance delta
        cur.execute(
            f"""
            SELECT py.id,
                   py.embedded_content = rs.embedded_content AS content_eq,
                   py.embedding <=> rs.embedding AS cos_dist
            FROM {args.py_schema}.{args.table} py
            JOIN {args.rs_schema}.{args.table} rs USING (id)
            ORDER BY py.id
            """
        )
        matches = cur.fetchall()

    # Write report
    out_dir = REPO_ROOT / "skill-output" / "rust-parity"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "report.md"

    top1_match = py_top[0][1] == rs_top[0][1] if py_top and rs_top else False
    content_match_rate = sum(1 for m in matches if m[1]) / max(1, len(matches))
    cos_dists = [float(m[2]) for m in matches]
    max_cos_dist = max(cos_dists) if cos_dists else 0.0
    mean_cos_dist = sum(cos_dists) / len(cos_dists) if cos_dists else 0.0

    lines = [
        "# chunkshop Python vs Rust parity report",
        "",
        f"- Corpus glob: `{args.corpus}`",
        f"- Query: `{args.query!r}`",
        f"- Python table: `{args.py_schema}.{args.table}`  rows={py_rows} docs={py_docs}",
        f"- Rust table:   `{args.rs_schema}.{args.table}`  rows={rs_rows} docs={rs_docs}",
        "",
        "## Top-k retrieval",
        "",
        f"- top-1 doc_id match: **{top1_match}**",
        f"- Python top-{args.topk}: {[(i, d) for i, d in py_top]}",
        f"- Rust top-{args.topk}:   {[(i, d) for i, d in rs_top]}",
        "",
        "## Chunk-level comparison",
        "",
        f"- rows with identical `embedded_content`: **{content_match_rate:.0%}** ({sum(1 for m in matches if m[1])}/{len(matches)})",
        f"- max cosine distance between matched embeddings: **{max_cos_dist:.6f}**",
        f"- mean cosine distance between matched embeddings: **{mean_cos_dist:.6f}**",
        "",
        "## Interpretation",
        "",
        "Cosine distance much greater than 1e-4 indicates different embeddings",
        "were produced for identical inputs. For chunkshop-rs MVP this is expected",
        "because fastembed-rs's `BGEBaseENV15Q` variant is Qdrant's fp32-optimized",
        "ONNX, not Xenova's int8-quantized one. The wire format (dim, table shape,",
        "ordering) is interchangeable; the numerical values differ by the amount",
        "you'd expect from fp32-vs-int8 drift.",
        "",
        "Chunks that match byte-for-byte on `embedded_content` prove the chunker",
        "itself is cross-language-identical.",
        "",
    ]
    report.write_text("\n".join(lines))
    log(f"wrote {report}")
    log(f"top-1 match: {top1_match}, max cos dist: {max_cos_dist:.6f}, content match: {content_match_rate:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
