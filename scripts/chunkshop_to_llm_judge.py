#!/usr/bin/env python3
"""Convert Chunkshop benchmark JSON into llm-judge JSONL cases.

Supported inputs:
- skill-output/benchmarks/v05_audited_judge.json
- skill-output/benchmarks/experiments/raw*.json

The output uses the `chunkshop-e1e8` llm-judge profile. Existing generated
answers are preserved by default; use `--blank-answers` when the next judge run
should generate fresh answers from the retrieved/summarized context.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def _hit_chunks(hits: list[dict[str, Any]]) -> list[str]:
    chunks = []
    for hit in hits:
        heading = ""
        meta = hit.get("metadata") or {}
        if isinstance(meta, dict):
            title = meta.get("title") or meta.get("heading")
            if title:
                heading = f"{title}\n"
        text = hit.get("embedded_text") or hit.get("text") or hit.get("original_content")
        if text:
            chunks.append(f"{heading}{text}".strip())
    return chunks


def _convert_audited(data: dict[str, Any], *, blank_answers: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in data.get("records", []):
        raw_chunks = _hit_chunks(record.get("hits") or [])
        base = {
            "question": record["question"],
            "gold_answer": record["gold_answer"],
            "required_facts": record.get("required_facts") or [],
            "retrieval_ms": record.get("retrieval_ms"),
            "question_class": record.get("question_class"),
            "caption_fact": record.get("caption_fact"),
            "raw_retrieved_chunks": raw_chunks,
            "settings": {
                "source": "v05_audited_judge",
                "question_id": record.get("id"),
            },
        }
        for treatment, tdata in (record.get("treatments") or {}).items():
            context = tdata.get("context", "")
            chunks = raw_chunks if treatment == "raw" else ([context] if context else [])
            row = dict(base)
            row.update(
                {
                    "id": f"{record['id']}::{treatment}",
                    "config_label": treatment,
                    "answer": "" if blank_answers else tdata.get("answer", ""),
                    "retrieved_chunks": chunks,
                    "retrieved_full_context": context,
                    "summarized_answer_context": context if treatment != "raw" else "",
                    "context": context,
                    "token_counts": {
                        "context": tdata.get("tokens"),
                        "chars": tdata.get("chars"),
                    },
                    "old_substring_score": tdata.get("score_a_substring"),
                    "old_substring_missing": tdata.get("score_a_missing") or [],
                    "old_judge_verdict": tdata.get("score_b_verdict"),
                    "old_judge_raw": tdata.get("judge_raw"),
                    "timing": {
                        "answer_ms": tdata.get("answer_ms"),
                        "judge_ms": tdata.get("judge_ms"),
                        "summary_ms": tdata.get("summary_ms"),
                    },
                    "settings": {
                        **base["settings"],
                        "treatment": treatment,
                    },
                }
            )
            rows.append(row)
    return rows


def _convert_experiments(data: dict[str, Any], *, blank_answers: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for experiment, configs in (data.get("experiments") or {}).items():
        for cfg in configs:
            label = cfg.get("label", "unknown")
            for q in cfg.get("per_q") or []:
                row = {
                    "id": f"{experiment}::{label}::{q['id']}",
                    "question": q.get("q"),
                    "gold_answer": q.get("gold"),
                    "required_facts": q.get("facts") or [],
                    "answer": "" if blank_answers else q.get("answer", ""),
                    "context": q.get("ctx") or "",
                    "retrieved_full_context": q.get("ctx") or "",
                    "retrieved_chunks": [q["ctx"]] if q.get("ctx") else [],
                    "config_label": f"{experiment}/{label}",
                    "question_class": q.get("qclass"),
                    "retrievable": q.get("retrievable"),
                    "token_counts": {
                        "context": q.get("tokens"),
                        "facts_present": q.get("n_facts"),
                        "facts_total": q.get("n_facts_total"),
                    },
                    "old_judge_verdict": q.get("verdict"),
                    "old_judge_raw": q.get("judge_raw"),
                    "timing": {
                        "answer_ms": q.get("answer_ms"),
                        "judge_ms": q.get("judge_ms"),
                    },
                    "settings": {
                        "source": "experiments",
                        "experiment": experiment,
                        "label": label,
                        "question_id": q.get("id"),
                    },
                }
                rows.append(row)
    return rows


def convert(path: Path, *, blank_answers: bool) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "records" in data:
        return _convert_audited(data, blank_answers=blank_answers)
    if "experiments" in data:
        return _convert_experiments(data, blank_answers=blank_answers)
    raise ValueError(f"unsupported Chunkshop benchmark JSON shape: {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument(
        "--limit",
        type=int,
        help="Write only the first N converted cases for smoke/probe runs.",
    )
    ap.add_argument(
        "--blank-answers",
        action="store_true",
        help="Leave answer empty so llm-judge --generate-answer creates it.",
    )
    args = ap.parse_args()

    rows = convert(args.input, blank_answers=args.blank_answers)
    if args.limit is not None:
        if args.limit < 0:
            raise ValueError("--limit must be >= 0")
        rows = rows[: args.limit]
    n = _write_jsonl(args.out, rows)
    print(f"wrote {n} cases to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
