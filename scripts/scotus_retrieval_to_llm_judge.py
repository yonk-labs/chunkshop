#!/usr/bin/env python3
"""Build SCOTUS retrieval-policy JSONL for llm-judge.

This is the first executable slice of the deep harness: take the 30 SCOTUS
questions, run real Chunkshop retrieval against the existing SCOTUS Postgres
table, materialize multiple context policies, and write JSONL rows that
`llm-judge --generate-answer` can answer and judge.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
PY_SRC = REPO / "python" / "src"
if str(PY_SRC) not in sys.path:
    sys.path.insert(0, str(PY_SRC))

from chunkshop.config import FastembedEmbedder  # noqa: E402
from chunkshop.embedders import load_embedder  # noqa: E402
from chunkshop.search import hybrid_search  # noqa: E402
from chunkshop.search_common import Hit, summarize_hits  # noqa: E402


DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
DEFAULT_QUESTIONS = Path(
    "/home/yonk/yonk-tools/pg-raggraph/benchmarks/age-bakeoff/questions/scotus.yaml"
)


def _load_questions(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list):
        raise ValueError(f"expected questions list in {path}")
    return questions


def _query_keywords(query: str, n: int = 6) -> list[str]:
    try:
        from lede.extract import top_terms
    except Exception:
        return []
    terms = top_terms(query, n=n, kinds=("words", "phrases"), with_scores=True)
    return [t.term for t in terms if t.score > 0.0]


def _lede_summarize(text: str, **kwargs: Any) -> str:
    from chunkshop.summarizers.lede import summarize

    return str(summarize(text, **kwargs))


def _raw_context(hits: list[Hit]) -> str:
    return "\n\n".join((h.embedded_text or h.text) for h in hits)


def _toc(hits: list[Hit]) -> str:
    headings = []
    seen = set()
    for hit in hits:
        heading = hit.metadata.get("heading") or hit.metadata.get("title")
        if not heading:
            continue
        key = str(heading).casefold()
        if key in seen:
            continue
        seen.add(key)
        headings.append(str(heading))
    return "\n".join(f"- {heading}" for heading in headings)


def _facts_context(
    hits: list[Hit],
    max_items: int = 24,
) -> str:
    facts = []
    seen = set()

    def add_fact(text: Any) -> None:
        if len(facts) >= max_items:
            return
        value = " ".join(str(text).split())
        if not value:
            return
        norm = value.casefold()
        if norm in seen:
            return
        seen.add(norm)
        facts.append(value)

    for hit in hits:
        lede_report = hit.metadata.get("lede_report")
        if isinstance(lede_report, dict):
            for fact in lede_report.get("key_facts") or []:
                add_fact(f"lede_fact: {fact}")
            report_metadata = lede_report.get("metadata") or {}
            if isinstance(report_metadata, dict):
                for date in report_metadata.get("dates") or []:
                    add_fact(f"lede_date: {date}")
                for amount in report_metadata.get("amounts") or []:
                    add_fact(f"lede_amount: {amount}")
                for entity in report_metadata.get("entities") or []:
                    add_fact(f"lede_entity: {entity}")
            spacy_metadata = lede_report.get("spacy_metadata") or {}
            if isinstance(spacy_metadata, dict):
                entities = spacy_metadata.get("entities") or {}
                if isinstance(entities, dict):
                    for label, values in entities.items():
                        for value in values:
                            add_fact(f"lede_entity.{label}: {value}")
        for key in ("heading", "title", "case", "issue", "author"):
            value = hit.metadata.get(key)
            if not value:
                continue
            add_fact(f"{key}: {value}")
        if len(facts) >= max_items:
            break
    return "\n".join(f"- {fact}" for fact in facts[:max_items])


def _policy_context(
    policy: str,
    hits: list[Hit],
    question: str,
    max_length: int,
) -> str:
    if policy == "raw_chunks":
        return _raw_context(hits)
    if policy == "summary":
        return summarize_hits(
            hits,
            _lede_summarize,
            max_length=max_length,
            hints=_query_keywords(question),
            prepend_headings=True,
            use_embedded=True,
        )
    if policy == "summary_toc":
        summary = _policy_context("summary", hits, question, max_length)
        toc = _toc(hits)
        return f"TOC:\n{toc}\n\nSUMMARY:\n{summary}" if toc else summary
    if policy == "summary_toc_facts":
        summary_toc = _policy_context("summary_toc", hits, question, max_length)
        facts = _facts_context(hits)
        return f"{summary_toc}\n\nEXTRACTED FACTS:\n{facts}" if facts else summary_toc
    raise ValueError(f"unknown context policy: {policy}")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--out", type=Path, default=Path(".llm-judge-inputs/scotus-30-retrieval.jsonl"))
    ap.add_argument("--dsn-env", default="CHUNKSHOP_TEST_DSN")
    ap.add_argument("--schema", default="chunkshop_fastmode_scotus")
    ap.add_argument("--table", default="chunks")
    ap.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    ap.add_argument("--dim", type=int, default=384)
    ap.add_argument("--top-k", type=int, nargs="+", default=[10, 25])
    ap.add_argument(
        "--policies",
        nargs="+",
        default=["raw_chunks", "summary_toc", "summary_toc_facts"],
        choices=["raw_chunks", "summary", "summary_toc", "summary_toc_facts"],
    )
    ap.add_argument("--summary-max-length", type=int, default=1200)
    ap.add_argument("--vector-metric", choices=["cosine", "inner_product", "l2"], default="cosine")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    dsn = os.environ.get(args.dsn_env) or DEFAULT_DSN
    questions = _load_questions(args.questions)
    if args.limit is not None:
        questions = questions[: args.limit]

    embedder = load_embedder(
        FastembedEmbedder(type="fastembed", model_name=args.model, dim=args.dim)
    )
    rows: list[dict[str, Any]] = []
    for q in questions:
        question = q["question"]
        t0 = time.perf_counter()
        qv = embedder.embed([question])[0]
        retrieval_ms = (time.perf_counter() - t0) * 1000.0
        max_k = max(args.top_k)
        hits = hybrid_search(
            dsn,
            schema=args.schema,
            table=args.table,
            query=question,
            query_vec=qv,
            k=max_k,
            legs=("semantic", "fts"),
            fusion="rrf",
            vector_metric=args.vector_metric,
        )
        for top_k in args.top_k:
            scoped_hits = hits[:top_k]
            for policy in args.policies:
                context = _policy_context(
                    policy,
                    scoped_hits,
                    question,
                    args.summary_max_length,
                )
                row_id = f"{q['id']}::{policy}_k{top_k}"
                rows.append(
                    {
                        "id": row_id,
                        "question": question,
                        "gold_answer": q.get("gold_answer", ""),
                        "required_facts": q.get("required_facts") or [],
                        "retrieved_chunks": [context],
                        "retrieved_full_context": context,
                        "answer": "",
                        "config_label": f"{policy}_k{top_k}",
                        "question_class": q.get("question_class"),
                        "retrieval_ms": retrieval_ms,
                        "token_counts": {"chars": len(context)},
                        "settings": {
                            "source": "scotus_retrieval_to_llm_judge",
                            "question_id": q.get("id"),
                            "context_policy": policy,
                            "top_k": top_k,
                            "retrieval_mode": "hybrid_rrf",
                            "vector_metric": args.vector_metric,
                            "summary_max_length": args.summary_max_length,
                        },
                    }
                )

    _write_jsonl(args.out, rows)
    print(
        f"wrote {len(rows)} cases ({len(questions)} questions x "
        f"{len(args.top_k)} k values x {len(args.policies)} policies) to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
