#!/usr/bin/env python3
"""Run the bucketed deep RAG sweep in resumable phases.

Phase 1 collects data:
- ingest SCOTUS once per embedder/chunker/vector metric cell
- retrieve all 50 SCOTUS bucket questions for each context/hints policy
- write JSONL rows and per-case audit Markdown before any judging happens
- build LongBench rows when a LongBench JSONL is available

Phase 2 judges the frozen JSONL with llm-judge.

The script is intentionally file-backed and restartable. Existing ingests,
JSONL files, judge outputs, and reports are reused unless --force-* flags are
passed.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[1]
PY_SRC = REPO / "python" / "src"
if str(PY_SRC) not in sys.path:
    sys.path.insert(0, str(PY_SRC))

from chunkshop.chunkers import load_chunker  # noqa: E402
from chunkshop.config import (  # noqa: E402
    FastembedEmbedder,
    FixedOverlapChunker,
    HierarchyChunker,
    SentenceAwareChunker,
)
from chunkshop.embedders import load_embedder  # noqa: E402
from chunkshop.search import hybrid_search  # noqa: E402
from chunkshop.search_common import Hit, summarize_hits  # noqa: E402
from chunkshop.sources.base import Document  # noqa: E402


DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
DEFAULT_SCOTUS_QUESTIONS = REPO / "docs/samples/eval/scotus-50-query-buckets.yaml"
DEFAULT_LONG_BENCH = REPO / "data/benchmarks/longbench.jsonl"
FALLBACK_LONG_BENCH = Path("/home/yonk/yonk-tools/llm-judge/examples/longbench_eval.jsonl")


@dataclass(frozen=True)
class EmbedderSpec:
    name: str
    model_name: str
    dim: int


@dataclass(frozen=True)
class ChunkerSpec:
    name: str
    config: dict[str, Any]


@dataclass(frozen=True)
class ContextSpec:
    name: str
    include_headers: bool
    source: str = "chunks"
    top_n_raw: int = 0
    max_docs: int = 5


EMBEDDERS = [
    EmbedderSpec("bge_small", "BAAI/bge-small-en-v1.5", 384),
    EmbedderSpec("bge_base", "BAAI/bge-base-en-v1.5", 768),
    EmbedderSpec("bge_large", "BAAI/bge-large-en-v1.5", 1024),
]

CHUNKERS = [
    ChunkerSpec("hierarchy", {"type": "hierarchy"}),
    ChunkerSpec("sentence_aware", {"type": "sentence_aware"}),
    ChunkerSpec("fixed_500_100", {"type": "fixed_overlap", "window_words": 500, "step_words": 100}),
]

VECTOR_METRICS = ["cosine", "inner_product", "l2"]

CONTEXTS = [
    ContextSpec("summary_meta", include_headers=False),
    ContextSpec("summary_meta_headers", include_headers=True),
    ContextSpec("doc_summary_meta", include_headers=False, source="documents"),
    ContextSpec("doc_summary_meta_headers", include_headers=True, source="documents"),
    ContextSpec("doc_summary_meta_headers_top5", include_headers=True, source="documents", top_n_raw=5),
]

HINT_MODES = [False, True]


def limit_items(items: list[Any], limit: int | None) -> list[Any]:
    return items[:limit] if limit else items


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()


def approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def run(cmd: list[str], *, cwd: Path, log: Path | None = None, env: dict[str, str] | None = None) -> None:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n$ {' '.join(cmd)}\n")
            f.flush()
            subprocess.run(cmd, cwd=cwd, env=merged_env, stdout=f, stderr=subprocess.STDOUT, check=True)
    else:
        subprocess.run(cmd, cwd=cwd, env=merged_env, check=True)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_scotus_questions(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list):
        raise ValueError(f"expected question list in {path}")
    return data if isinstance(data, dict) else {}, questions


def query_hints(query: str, n: int = 8) -> list[str]:
    try:
        from lede.extract import top_terms
    except Exception:
        return []
    terms = top_terms(query, n=n, kinds=("words", "phrases"), with_scores=True)
    return [t.term for t in terms if t.score > 0.0]


def lede_summarize(text: str, *, hints: list[str] | None = None, max_length: int = 1200) -> str:
    try:
        from chunkshop.summarizers.lede import summarize
    except Exception:
        return text[:max_length]
    kwargs: dict[str, Any] = {"max_length": max_length}
    if hints:
        kwargs["hints"] = hints
    return str(summarize(text, **kwargs))


def hit_title(hit: Hit) -> str:
    return str(
        hit.metadata.get("heading")
        or hit.metadata.get("title")
        or hit.metadata.get("source_path")
        or f"{hit.doc_id}:{hit.seq_num}"
    )


def raw_context(hits: list[Hit]) -> str:
    return "\n\n".join((h.embedded_text or h.text) for h in hits)


def facts_context(hits: list[Hit], max_items: int = 40) -> str:
    facts: list[str] = []
    seen: set[str] = set()

    def add(value: Any, prefix: str = "") -> None:
        if len(facts) >= max_items:
            return
        text = " ".join(str(value).split())
        if not text:
            return
        item = f"{prefix}: {text}" if prefix else text
        key = item.casefold()
        if key in seen:
            return
        seen.add(key)
        facts.append(item)

    for hit in hits:
        report = hit.metadata.get("lede_report")
        if isinstance(report, dict):
            attrs = report.get("attributes") or {}
            if isinstance(attrs, dict):
                for key, rec in attrs.items():
                    if isinstance(rec, dict) and rec.get("value") is not None:
                        add(rec["value"], f"attribute.{key}")
            for fact in report.get("key_facts") or []:
                add(fact, "lede_fact")
            for fact in report.get("fact_records") or []:
                if isinstance(fact, dict):
                    subj = fact.get("subject") or ""
                    pred = fact.get("predicate") or ""
                    obj = fact.get("object") or ""
                    add(" ".join(str(x) for x in (subj, pred, obj) if x), "fact_record")
            metadata = report.get("metadata") or {}
            if isinstance(metadata, dict):
                for key in ("dates", "amounts", "entities"):
                    for value in metadata.get(key) or []:
                        add(value, f"lede_{key[:-1]}")
            spacy = report.get("spacy_metadata") or {}
            if isinstance(spacy, dict):
                entities = spacy.get("entities") or {}
                if isinstance(entities, dict):
                    for label, values in entities.items():
                        for value in values:
                            add(value, f"entity.{label}")
        for key in ("heading", "title", "case", "issue", "author"):
            if hit.metadata.get(key):
                add(hit.metadata[key], key)
        if len(facts) >= max_items:
            break
    return "\n".join(f"- {fact}" for fact in facts)


def fetch_document_records(dsn: str, schema: str, doc_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not doc_ids:
        return {}
    try:
        import psycopg
    except Exception:
        return {}
    unique_doc_ids = list(dict.fromkeys(doc_ids))
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT doc_id, title, lede_summary, lede_toc, lede_facts, '
                f'lede_report, lede_search_text, full_content, chunk_count '
                f'FROM "{schema}"."documents" WHERE doc_id = ANY(%s)',
                (unique_doc_ids,),
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    return {
        row[0]: {
            "doc_id": row[0],
            "title": row[1],
            "lede_summary": row[2],
            "lede_toc": row[3] or [],
            "lede_facts": row[4] or [],
            "lede_report": row[5] or {},
            "lede_search_text": row[6],
            "full_content": row[7] or "",
            "chunk_count": row[8],
        }
        for row in rows
    }


def document_facts_context(records: list[dict[str, Any]], max_items: int = 60) -> str:
    facts: list[str] = []
    seen: set[str] = set()

    def add(value: Any, prefix: str = "") -> None:
        if len(facts) >= max_items:
            return
        if isinstance(value, dict):
            subject = value.get("subject") or ""
            predicate = value.get("predicate") or ""
            obj = value.get("object")
            value = " ".join(str(part) for part in (subject, predicate, obj) if part)
        text = " ".join(str(value).split())
        if not text:
            return
        item = f"{prefix}: {text}" if prefix else text
        key = item.casefold()
        if key in seen:
            return
        seen.add(key)
        facts.append(item)

    for rec in records:
        for fact in rec.get("lede_facts") or []:
            add(fact, "doc_fact")
        report = rec.get("lede_report") or {}
        attrs = report.get("attributes") or {}
        if isinstance(attrs, dict):
            for key, attr in attrs.items():
                if isinstance(attr, dict) and attr.get("value") is not None:
                    add(attr["value"], f"attribute.{key}")
        if len(facts) >= max_items:
            break
    return "\n".join(f"- {fact}" for fact in facts)


def context_from_hits(
    hits: list[Hit],
    *,
    question: str,
    context: ContextSpec,
    use_hints: bool,
    max_summary_length: int,
) -> tuple[str, dict[str, Any]]:
    hints = query_hints(question) if use_hints else []
    summary = summarize_hits(
        hits,
        lambda text, **_kwargs: lede_summarize(text, hints=hints, max_length=max_summary_length),
        max_length=max_summary_length,
        hints=hints,
        prepend_headings=context.include_headers,
        use_embedded=True,
    )
    parts = []
    if context.include_headers:
        seen = set()
        headings = []
        for hit in hits:
            title = hit_title(hit)
            key = title.casefold()
            if key not in seen:
                seen.add(key)
                headings.append(f"- {title}")
        if headings:
            parts.append("HEADERS:\n" + "\n".join(headings))
    parts.append("SUMMARY:\n" + summary)
    facts = facts_context(hits)
    if facts:
        parts.append("METADATA AND FACTS:\n" + facts)
    packed = "\n\n".join(parts)
    raw = raw_context(hits)
    raw_tokens = approx_tokens(raw)
    packed_tokens = approx_tokens(packed)
    return packed, {
        "raw_context_chars": len(raw),
        "context_chars": len(packed),
        "raw_context_tokens_est": raw_tokens,
        "context_tokens_est": packed_tokens,
        "token_reduction_vs_raw_context": 1.0 - (packed_tokens / raw_tokens) if raw_tokens else 0.0,
        "hints": hints,
    }


def context_from_documents(
    hits: list[Hit],
    *,
    dsn: str,
    schema: str,
    question: str,
    context: ContextSpec,
    use_hints: bool,
    max_summary_length: int,
) -> tuple[str, dict[str, Any]]:
    del max_summary_length
    hints = query_hints(question) if use_hints else []
    doc_ids = list(dict.fromkeys(hit.doc_id for hit in hits))[: context.max_docs]
    record_map = fetch_document_records(dsn, schema, doc_ids)
    records = [record_map[doc_id] for doc_id in doc_ids if doc_id in record_map]
    if not records:
        return context_from_hits(
            hits,
            question=question,
            context=ContextSpec(context.name + "_fallback", include_headers=context.include_headers),
            use_hints=use_hints,
            max_summary_length=1200,
        )

    parts = []
    if context.include_headers:
        headers = []
        for rec in records:
            title = rec.get("title") or rec["doc_id"]
            headers.append(f"- {title}")
            toc = rec.get("lede_toc") or []
            if isinstance(toc, list):
                headers.extend(f"  - {item}" for item in toc[:12])
        if headers:
            parts.append("HEADERS:\n" + "\n".join(headers))

    summaries = []
    for rec in records:
        title = rec.get("title") or rec["doc_id"]
        summary = rec.get("lede_summary") or rec.get("lede_search_text") or ""
        if summary:
            summaries.append(f"### {title}\n{summary}")
    if summaries:
        parts.append("DOCUMENT SUMMARIES:\n" + "\n\n".join(summaries))

    facts = document_facts_context(records)
    if facts:
        parts.append("DOCUMENT METADATA AND FACTS:\n" + facts)

    if context.top_n_raw:
        parts.append("TOP RAW CHUNKS:\n" + raw_context(hits[: context.top_n_raw]))

    packed = "\n\n".join(parts)
    raw = raw_context(hits)
    raw_tokens = approx_tokens(raw)
    packed_tokens = approx_tokens(packed)
    full_doc_raw = "\n\n".join(rec.get("full_content") or "" for rec in records)
    full_doc_tokens = approx_tokens(full_doc_raw) if full_doc_raw else 0
    return packed, {
        "raw_context_chars": len(raw),
        "context_chars": len(packed),
        "raw_context_tokens_est": raw_tokens,
        "context_tokens_est": packed_tokens,
        "token_reduction_vs_raw_context": 1.0 - (packed_tokens / raw_tokens) if raw_tokens else 0.0,
        "full_doc_tokens_est": full_doc_tokens,
        "token_reduction_vs_full_docs": 1.0 - (packed_tokens / full_doc_tokens) if full_doc_tokens else 0.0,
        "document_count": len(records),
        "hints": hints,
    }


def ingest_config(
    *,
    schema: str,
    embedder: EmbedderSpec,
    chunker: ChunkerSpec,
    vector_metric: str,
) -> dict[str, Any]:
    return {
        "cell_name": schema,
        "source": {
            "type": "files",
            "glob": "/home/yonk/yonk-tools/pg-raggraph/benchmarks/scotus/*.md",
            "id_from": "stem",
        },
        "chunker": chunker.config,
        "embedder": {
            "type": "fastembed",
            "model_name": embedder.model_name,
            "dim": embedder.dim,
            "threads": 4,
        },
        "extractor": {
            "type": "lede_report",
            "max_chars": 4000,
            "max_facts": 40,
            "backend": "regex",
            "keep_headings": True,
            "include_toc": True,
        },
        "target": {
            "type": "postgres",
            "dsn_env": "CHUNKSHOP_TEST_DSN",
            "database": schema,
            "table": "chunks",
            "mode": "overwrite",
            "hnsw": True,
            "vector_metric": vector_metric,
            "promote_metadata": [
                {"path": "lede_report.attributes.term.value", "type": "text"},
                {"path": "lede_report.attributes.docket_number.value", "type": "text"},
                {"path": "lede_report.attributes.citation.value", "type": "text"},
            ],
            "fts": {
                "enabled": True,
                "language": "english",
                "include_metadata_paths": ["lede_report.search_text"],
            },
            "documents": {
                "enabled": True,
                "table": "documents",
                "store_full_content": True,
                "store_lede_report": True,
                "promote_metadata": [
                    {"path": "lede_report.attributes.term.value", "type": "text"},
                    {"path": "lede_report.attributes.docket_number.value", "type": "text"},
                    {"path": "lede_report.attributes.citation.value", "type": "text"},
                ],
                "fts": {"enabled": True, "language": "english"},
            },
        },
        "runtime": {"omp_num_threads": 4, "heartbeat_every": 25},
    }


def schema_name(embedder: EmbedderSpec, chunker: ChunkerSpec, vector_metric: str) -> str:
    return f"chunkshop_s50_{slug(embedder.name)}_{slug(chunker.name)}_{slug(vector_metric)}"


def table_has_rows(dsn: str, schema: str) -> bool:
    try:
        import psycopg
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass(%s)",
                (f'"{schema}"."chunks"',),
            )
            if cur.fetchone()[0] is None:
                return False
            cur.execute(f'SELECT count(*) FROM "{schema}"."chunks"')
            if int(cur.fetchone()[0]) <= 0:
                return False
            cur.execute(
                "SELECT to_regclass(%s)",
                (f'"{schema}"."documents"',),
            )
            if cur.fetchone()[0] is None:
                return False
            cur.execute(f'SELECT count(*) FROM "{schema}"."documents"')
            return int(cur.fetchone()[0]) > 0
    except Exception:
        return False


def collect_scotus(args: argparse.Namespace, out_dir: Path) -> Path:
    _fixture, questions = load_scotus_questions(args.scotus_questions)
    questions = limit_items(questions, args.question_limit)
    rows_path = out_dir / "inputs/scotus-50-matrix.jsonl"
    audits_dir = out_dir / "audits/scotus"
    if rows_path.exists() and not args.force_collect:
        return rows_path

    configs_dir = out_dir / "configs/scotus-ingest"
    logs_dir = out_dir / "logs/ingest"
    configs_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    for embedder in limit_items(EMBEDDERS, args.embedder_limit):
        embedder_cfg = FastembedEmbedder(type="fastembed", model_name=embedder.model_name, dim=embedder.dim, threads=4)
        embedder_impl = load_embedder(embedder_cfg)
        for chunker in limit_items(CHUNKERS, args.chunker_limit):
            for metric in limit_items(VECTOR_METRICS, args.metric_limit):
                schema = schema_name(embedder, chunker, metric)
                cfg_path = configs_dir / f"{schema}.yaml"
                cfg_path.write_text(
                    yaml.safe_dump(
                        ingest_config(schema=schema, embedder=embedder, chunker=chunker, vector_metric=metric),
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                if args.force_ingest or not table_has_rows(args.dsn, schema):
                    run(
                        ["uv", "run", "--extra", "lede", "chunkshop", "ingest", "--config", f"../{cfg_path.relative_to(REPO)}"],
                        cwd=REPO / "python",
                        env={"CHUNKSHOP_TEST_DSN": args.dsn},
                        log=logs_dir / f"{schema}.log",
                    )

                for q in questions:
                    question = q["question"]
                    t0 = time.perf_counter()
                    qv = embedder_impl.embed([question])[0]
                    hits = hybrid_search(
                        args.dsn,
                        schema=schema,
                        table="chunks",
                        query=question,
                        query_vec=qv,
                        k=args.top_k,
                        legs=("semantic", "fts"),
                        fusion="rrf",
                        vector_metric=metric,
                    )
                    retrieval_ms = (time.perf_counter() - t0) * 1000.0
                    for context in CONTEXTS:
                        for use_hints in HINT_MODES:
                            if context.source == "documents":
                                packed, token_counts = context_from_documents(
                                    hits,
                                    dsn=args.dsn,
                                    schema=schema,
                                    question=question,
                                    context=context,
                                    use_hints=use_hints,
                                    max_summary_length=args.summary_max_length,
                                )
                            else:
                                packed, token_counts = context_from_hits(
                                    hits,
                                    question=question,
                                    context=context,
                                    use_hints=use_hints,
                                    max_summary_length=args.summary_max_length,
                                )
                            label = (
                                f"{embedder.name}__{chunker.name}__{metric}__"
                                f"{context.name}__hints_{'on' if use_hints else 'off'}"
                            )
                            case_id = f"{q['id']}::{label}"
                            settings = {
                                "workload": "scotus_50_buckets",
                                "config_label": label,
                                "question_id": q["id"],
                                "bucket": q.get("bucket"),
                                "rag_applicable": q.get("rag_applicable"),
                                "retrieval_contract": q.get("retrieval_contract"),
                                "embedder": embedder.name,
                                "embedder_model": embedder.model_name,
                                "chunker": chunker.name,
                                "vector_metric": metric,
                                "retrieval_mode": "hybrid_rrf",
                                "top_k": args.top_k,
                                "context_policy": context.name,
                                "hints": use_hints,
                                "summary_max_length": args.summary_max_length,
                                "token_counts": token_counts,
                            }
                            row = {
                                "id": case_id,
                                "question": question,
                                "gold_answer": q.get("gold_answer", ""),
                                "required_facts": q.get("required_facts") or [],
                                "retrieved_chunks": [packed],
                                "retrieved_full_context": packed,
                                "answer": "",
                                "config_label": label,
                                "question_class": q.get("bucket"),
                                "retrieval_ms": retrieval_ms,
                                "settings": settings,
                            }
                            rows.append(row)
                            write_case_audit(
                                audits_dir / f"{slug(case_id)}.md",
                                row=row,
                                hits=hits,
                                packed=packed,
                                token_counts=token_counts,
                            )
    write_jsonl(rows_path, rows)
    return rows_path


def write_case_audit(path: Path, *, row: dict[str, Any], hits: list[Hit], packed: str, token_counts: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = row.get("settings") or {}
    lines = [
        f"# {row['id']}",
        "",
        f"- Workload: `{settings.get('workload')}`",
        f"- Bucket: `{settings.get('bucket')}`",
        f"- Config: `{settings.get('config_label')}`",
        f"- Expected: {row.get('gold_answer', '')}",
        f"- Required facts: {', '.join(row.get('required_facts') or [])}",
        f"- Retrieval ms: {row.get('retrieval_ms', 0):.2f}",
        f"- Context tokens est: {token_counts.get('context_tokens_est')}",
        f"- Token reduction vs raw context: {token_counts.get('token_reduction_vs_raw_context', 0.0):.1%}",
        "",
        "## Question",
        "",
        row["question"],
        "",
        "## Packed Context",
        "",
        "```text",
        packed[:24000],
        "```",
        "",
        "## Hits",
        "",
    ]
    for i, hit in enumerate(hits, start=1):
        lines.extend(
            [
                f"### Hit {i}",
                "",
                f"- doc_id: `{hit.doc_id}`",
                f"- seq_num: `{hit.seq_num}`",
                f"- score: `{hit.score:.6f}`",
                f"- legs: `{','.join(hit.legs)}`",
                f"- title: {hit_title(hit)}",
                "",
                "```text",
                (hit.embedded_text or hit.text)[:4000],
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def metric_score(query_vec: np.ndarray, chunk_vecs: np.ndarray, metric: str) -> np.ndarray:
    if metric == "cosine":
        q = query_vec / max(np.linalg.norm(query_vec), 1e-12)
        c = chunk_vecs / np.maximum(np.linalg.norm(chunk_vecs, axis=1, keepdims=True), 1e-12)
        return c @ q
    if metric == "inner_product":
        return chunk_vecs @ query_vec
    if metric == "l2":
        return -np.linalg.norm(chunk_vecs - query_vec, axis=1)
    raise ValueError(metric)


def longbench_rows(input_path: Path, args: argparse.Namespace, out_dir: Path) -> Path | None:
    if not input_path.exists():
        return None
    rows_path = out_dir / "inputs/longbench-matrix.jsonl"
    audits_dir = out_dir / "audits/longbench"
    if rows_path.exists() and not args.force_collect:
        return rows_path
    source_rows = read_jsonl(input_path)
    if args.longbench_limit:
        source_rows = source_rows[: args.longbench_limit]

    out_rows: list[dict[str, Any]] = []
    for embedder in limit_items(EMBEDDERS, args.embedder_limit):
        embedder_cfg = FastembedEmbedder(type="fastembed", model_name=embedder.model_name, dim=embedder.dim, threads=4)
        embedder_impl = load_embedder(embedder_cfg)
        for chunker in limit_items(CHUNKERS, args.chunker_limit):
            chunker_obj = load_chunker(_chunker_cfg(chunker), main_embedder=embedder_cfg)
            for record_i, rec in enumerate(source_rows, start=1):
                question = rec.get("input") or rec.get("question") or rec.get("query") or ""
                context_text = rec.get("context") or rec.get("full_context") or rec.get("document") or ""
                answers = rec.get("answers") or rec.get("answer") or rec.get("reference") or []
                expected = answers[0] if isinstance(answers, list) and answers else str(answers)
                chunks = chunker_obj.chunk(Document(id=f"longbench-{record_i:04d}", content=context_text, title=f"longbench-{record_i:04d}", metadata={}))
                if not chunks:
                    continue
                embedded_texts = [c.embedded_content or c.original_content for c in chunks]
                chunk_vecs = np.asarray(embedder_impl.embed(embedded_texts))
                qv = np.asarray(embedder_impl.embed([question])[0])
                for metric in limit_items(VECTOR_METRICS, args.metric_limit):
                    scores = metric_score(qv, chunk_vecs, metric)
                    top_idx = list(np.argsort(scores)[::-1][: args.top_k])
                    hits = [
                        Hit(
                            doc_id=chunks[i].doc_id,
                            seq_num=chunks[i].seq_num,
                            text=chunks[i].original_content,
                            metadata=chunks[i].metadata or {},
                            score=float(scores[i]),
                            legs=("semantic",),
                            embedded_text=chunks[i].embedded_content,
                        )
                        for i in top_idx
                    ]
                    for context in CONTEXTS:
                        if context.source != "chunks":
                            continue
                        for use_hints in HINT_MODES:
                            packed, token_counts = context_from_hits(
                                hits,
                                question=question,
                                context=context,
                                use_hints=use_hints,
                                max_summary_length=args.summary_max_length,
                            )
                            oracle_tokens = approx_tokens(context_text)
                            token_counts["oracle_tokens_est"] = oracle_tokens
                            token_counts["token_reduction_vs_oracle"] = (
                                1.0 - token_counts["context_tokens_est"] / oracle_tokens
                                if oracle_tokens else 0.0
                            )
                            label = (
                                f"{embedder.name}__{chunker.name}__{metric}__"
                                f"{context.name}__hints_{'on' if use_hints else 'off'}"
                            )
                            case_id = f"longbench-{record_i:04d}::{label}"
                            settings = {
                                "workload": "longbench",
                                "config_label": label,
                                "question_id": f"longbench-{record_i:04d}",
                                "embedder": embedder.name,
                                "embedder_model": embedder.model_name,
                                "chunker": chunker.name,
                                "vector_metric": metric,
                                "retrieval_mode": "semantic_in_memory",
                                "top_k": args.top_k,
                                "context_policy": context.name,
                                "hints": use_hints,
                                "summary_max_length": args.summary_max_length,
                                "token_counts": token_counts,
                                "dataset": rec.get("dataset"),
                            }
                            row = {
                                "id": case_id,
                                "question": question,
                                "gold_answer": expected,
                                "required_facts": answers if isinstance(answers, list) else [expected],
                                "retrieved_chunks": [packed],
                                "retrieved_full_context": packed,
                                "answer": "",
                                "config_label": label,
                                "question_class": rec.get("dataset") or "longbench",
                                "retrieval_ms": 0.0,
                                "settings": settings,
                            }
                            out_rows.append(row)
                            write_case_audit(
                                audits_dir / f"{slug(case_id)}.md",
                                row=row,
                                hits=hits,
                                packed=packed,
                                token_counts=token_counts,
                            )
    write_jsonl(rows_path, out_rows)
    return rows_path


def _chunker_cfg(spec: ChunkerSpec):
    if spec.config["type"] == "hierarchy":
        return HierarchyChunker.model_validate(spec.config)
    if spec.config["type"] == "sentence_aware":
        return SentenceAwareChunker.model_validate(spec.config)
    if spec.config["type"] == "fixed_overlap":
        return FixedOverlapChunker.model_validate(spec.config)
    raise ValueError(spec.config["type"])


def llm_judge_config(input_path: Path, out_path: Path, out_dir: Path, profile: str) -> Path:
    cfg = {
        "input": str(input_path),
        "profile": profile,
        "out": str(out_path),
        "mode": "accurate",
        "generate_answer": True,
        "cache_dir": str(out_dir / "llm-judge-cache"),
        "resume": True,
        "concurrency": 4,
        "retries": 2,
        "parse_retries": 1,
        "timeout": 180,
        "temperature": 0,
        "max_tokens": 1200,
        "strict_json_fallback": True,
        "answer": {
            "provider": "openai-compatible",
            "base_url": "http://192.168.1.193:8000/v1",
            "model": "Intel/Qwen3-Coder-Next-int4-AutoRound",
            "max_tokens": 700,
        },
        "judges": [
            {
                "provider": "openai-compatible",
                "base_url": "http://192.168.1.193:8000/v1",
                "model": "Intel/Qwen3-Coder-Next-int4-AutoRound",
            },
            {
                "provider": "openai-compatible",
                "base_url": "http://192.168.1.133:8000/v1",
                "model": "cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit",
            },
        ],
    }
    config_path = out_dir / "configs/llm-judge" / f"{out_path.name}.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return config_path


def run_judge(config_path: Path, log_path: Path) -> None:
    run([".venv-llm-judge/bin/llm-judge", "evaluate", "--config", str(config_path)], cwd=REPO, log=log_path)


def summarize_results(run_dir: Path, report_path: Path) -> None:
    result_path = run_dir / "results.jsonl"
    if not result_path.exists():
        return
    rows = read_jsonl(result_path)
    by_config: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_bucket_config: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        settings = row.get("settings") or {}
        config = settings.get("config_label", "unknown")
        bucket = settings.get("bucket") or settings.get("dataset") or row.get("question_class") or "unknown"
        by_config[config].append(row)
        by_bucket[bucket].append(row)
        by_bucket_config[(bucket, config)].append(row)

    def stats(group: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(group)
        if not n:
            return {"n": 0, "accuracy": 0.0, "score": 0.0, "latency_ms": 0.0, "tokens": 0.0, "reduction": 0.0}
        passed = sum(1 for r in group if r.get("passed"))
        score = sum(float(r.get("score") or 0.0) for r in group) / n
        latency = sum(float(r.get("latency_ms") or 0.0) for r in group) / n
        tokens = []
        reductions = []
        for r in group:
            settings = r.get("settings") or {}
            tc = settings.get("token_counts") or {}
            if "context_tokens_est" in tc:
                tokens.append(float(tc["context_tokens_est"]))
            if "token_reduction_vs_raw_context" in tc:
                reductions.append(float(tc["token_reduction_vs_raw_context"]))
            elif "token_reduction_vs_oracle" in tc:
                reductions.append(float(tc["token_reduction_vs_oracle"]))
        return {
            "n": n,
            "accuracy": passed / n,
            "score": score,
            "latency_ms": latency,
            "tokens": sum(tokens) / len(tokens) if tokens else 0.0,
            "reduction": sum(reductions) / len(reductions) if reductions else 0.0,
        }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = report_path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["bucket", "config", "cases", "accuracy", "score", "avg_latency_ms", "avg_context_tokens", "avg_token_reduction"],
        )
        writer.writeheader()
        for (bucket, config), group in sorted(by_bucket_config.items()):
            s = stats(group)
            writer.writerow(
                {
                    "bucket": bucket,
                    "config": config,
                    "cases": s["n"],
                    "accuracy": f"{s['accuracy']:.4f}",
                    "score": f"{s['score']:.4f}",
                    "avg_latency_ms": f"{s['latency_ms']:.1f}",
                    "avg_context_tokens": f"{s['tokens']:.1f}",
                    "avg_token_reduction": f"{s['reduction']:.4f}",
                }
            )

    lines = ["# Deep Bucket Sweep Summary", ""]
    lines += ["## Buckets", "", "| Bucket | Cases | Accuracy | Score | Avg latency ms | Avg ctx tokens | Avg token reduction |", "|---|---:|---:|---:|---:|---:|---:|"]
    for bucket, group in sorted(by_bucket.items()):
        s = stats(group)
        lines.append(
            f"| `{bucket}` | {s['n']} | {s['accuracy']:.1%} | {s['score']:.3f} | "
            f"{s['latency_ms']:.0f} | {s['tokens']:.0f} | {s['reduction']:.1%} |"
        )
    lines += ["", "## Top Configs", "", "| Config | Cases | Accuracy | Score | Avg latency ms | Avg ctx tokens | Avg token reduction |", "|---|---:|---:|---:|---:|---:|---:|"]
    ranked = sorted(by_config.items(), key=lambda kv: (stats(kv[1])["accuracy"], stats(kv[1])["score"]), reverse=True)
    for config, group in ranked[:30]:
        s = stats(group)
        lines.append(
            f"| `{config}` | {s['n']} | {s['accuracy']:.1%} | {s['score']:.3f} | "
            f"{s['latency_ms']:.0f} | {s['tokens']:.0f} | {s['reduction']:.1%} |"
        )
    lines += ["", f"Full bucket/config CSV: `{csv_path}`", ""]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=Path("skill-output/eval/deep-bucket-sweep"))
    ap.add_argument("--dsn", default=os.environ.get("CHUNKSHOP_TEST_DSN", DEFAULT_DSN))
    ap.add_argument("--scotus-questions", type=Path, default=DEFAULT_SCOTUS_QUESTIONS)
    ap.add_argument("--longbench-input", type=Path, default=DEFAULT_LONG_BENCH)
    ap.add_argument("--use-longbench-example", action="store_true")
    ap.add_argument("--longbench-limit", type=int)
    ap.add_argument("--top-k", type=int, default=25)
    ap.add_argument("--summary-max-length", type=int, default=1200)
    ap.add_argument("--embedder-limit", type=int)
    ap.add_argument("--chunker-limit", type=int)
    ap.add_argument("--metric-limit", type=int)
    ap.add_argument("--question-limit", type=int)
    ap.add_argument("--force-ingest", action="store_true")
    ap.add_argument("--force-collect", action="store_true")
    ap.add_argument("--skip-judge", action="store_true")
    args = ap.parse_args()

    out_dir = args.out_dir if args.out_dir.is_absolute() else REPO / args.out_dir
    args.scotus_questions = (
        args.scotus_questions if args.scotus_questions.is_absolute() else REPO / args.scotus_questions
    )
    args.longbench_input = args.longbench_input if args.longbench_input.is_absolute() else REPO / args.longbench_input
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "embedders": [e.__dict__ for e in EMBEDDERS],
        "chunkers": [{"name": c.name, "config": c.config} for c in CHUNKERS],
        "vector_metrics": VECTOR_METRICS,
        "contexts": [c.__dict__ for c in CONTEXTS],
        "hint_modes": HINT_MODES,
        "top_k": args.top_k,
        "summary_max_length": args.summary_max_length,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    scotus_input = collect_scotus(args, out_dir)
    longbench_input = args.longbench_input
    if args.use_longbench_example and not longbench_input.exists():
        longbench_input = FALLBACK_LONG_BENCH
    longbench_matrix = longbench_rows(longbench_input, args, out_dir)

    if args.skip_judge:
        return 0

    scotus_run = out_dir / "llm-judge-runs/scotus-50-matrix"
    scotus_cfg = llm_judge_config(scotus_input, scotus_run, out_dir, "chunkshop-e1e8")
    run_judge(scotus_cfg, out_dir / "logs/llm-judge-scotus.log")
    summarize_results(scotus_run, out_dir / "reports/scotus-50-summary.md")

    if longbench_matrix is not None:
        lb_run = out_dir / "llm-judge-runs/longbench-matrix"
        lb_cfg = llm_judge_config(longbench_matrix, lb_run, out_dir, "chunkshop-e1e8")
        run_judge(lb_cfg, out_dir / "logs/llm-judge-longbench.log")
        summarize_results(lb_run, out_dir / "reports/longbench-summary.md")
    else:
        (out_dir / "reports/longbench-summary.md").write_text(
            "LongBench input not found. Provide --longbench-input data/benchmarks/longbench.jsonl.\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
