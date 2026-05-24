from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from chunkshop.cli import cli
from chunkshop.eval import build_eval_plan, load_eval_matrix, write_eval_plan
from chunkshop.search_common import Hit


ROOT = Path(__file__).resolve().parents[3]


def test_showcase_eval_matrix_expands_baselines_and_candidates() -> None:
    cfg = load_eval_matrix(ROOT / "docs/samples/eval/showcase-matrix.yaml")

    plan = build_eval_plan(cfg)

    assert plan.name == "chunkshop_showcase"
    assert len(plan.workloads) == 1
    assert [p.name for p in plan.policies] == [
        "oracle_full_context",
        "classic_rag_500tok_top25",
        "shipped_fast_mode",
        "raw_chunks",
    ]
    assert len(plan.runs) == 4


def test_deep_eval_profile_selection_expands_easy_mode() -> None:
    cfg = load_eval_matrix(ROOT / "docs/samples/eval/deep-matrix.yaml")

    plan = build_eval_plan(cfg, profiles=["general_default"])

    # 3 baselines + 1 embedder * 2 chunkers * 1 metric * 1 retrieval
    # * 2 candidate sets * 1 query expansion * 1 context packer.
    assert len(plan.policies) == 7
    assert len(plan.runs) == 49
    names = {p.name for p in plan.policies}
    assert "classic_rag_500tok_top25" in names
    assert any(name.startswith("general_default__") for name in names)
    workloads = {w["name"] for w in plan.workloads}
    assert "scotus_50_buckets" in workloads
    assert "longbench" in workloads


def test_scotus_50_query_buckets_are_balanced_and_explicit() -> None:
    fixture = yaml.safe_load((ROOT / "docs/samples/eval/scotus-50-query-buckets.yaml").read_text())

    questions = fixture["questions"]
    buckets = fixture["buckets"]
    ids = [q["id"] for q in questions]

    assert len(questions) == 50
    assert len(ids) == len(set(ids))
    assert set(buckets) == {q["bucket"] for q in questions}

    by_bucket = {bucket: 0 for bucket in buckets}
    for question in questions:
        by_bucket[question["bucket"]] += 1
        assert question["question"]
        assert question["gold_answer"]
        assert question["required_facts"]
        assert question["retrieval_contract"]

    assert by_bucket == {bucket: spec["count"] for bucket, spec in buckets.items()}

    impossible = [
        q for q in questions
        if q["bucket"] == "impossible_for_llm_topk_rag"
    ]
    assert len(impossible) == 10
    assert all(q["rag_applicable"] is False for q in impossible)
    assert all(q["retrieval_contract"] == "exhaustive_metadata_query" for q in impossible)


def test_write_eval_plan_materializes_manifest_report_and_judge_configs(tmp_path: Path) -> None:
    cfg = load_eval_matrix(ROOT / "docs/samples/eval/showcase-matrix.yaml")
    plan = build_eval_plan(cfg)

    written = write_eval_plan(cfg, plan, tmp_path, smoke_limit=2)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["name"] == "chunkshop_showcase"
    assert (tmp_path / "report.md").exists()
    assert len(written.llm_judge_configs) == 2

    smoke = yaml.safe_load((tmp_path / "llm-judge/scotus-smoke.yaml").read_text())
    assert smoke["mode"] == "quick"
    assert smoke["limit"] == 2
    assert smoke["profile"] == "chunkshop-e1e8"

    final = yaml.safe_load((tmp_path / "llm-judge/scotus-final.yaml").read_text())
    assert final["mode"] == "accurate"
    assert "limit" not in final
    assert len(final["judges"]) == 2


def test_eval_cli_validate_and_plan(tmp_path: Path) -> None:
    runner = CliRunner()
    config = ROOT / "docs/samples/eval/showcase-matrix.yaml"

    result = runner.invoke(cli, ["eval", "validate", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "expanded policies: 4" in result.output

    out = tmp_path / "plan"
    result = runner.invoke(
        cli,
        [
            "eval",
            "plan",
            "--config",
            str(config),
            "--out",
            str(out),
            "--smoke-limit",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out / "manifest.json").exists()
    assert (out / "report.md").exists()


def test_scotus_facts_context_uses_chunkshop_lede_report_metadata() -> None:
    script = ROOT / "scripts/scotus_retrieval_to_llm_judge.py"
    spec = importlib.util.spec_from_file_location("scotus_retrieval_to_llm_judge", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    hit = Hit(
        doc_id="snyder",
        seq_num=0,
        text="",
        score=1.0,
        metadata={
            "heading": "Snyder v. United States: Decision",
            "lede_report": {
                "key_facts": ["Justice Ketanji Brown Jackson dissented."],
                "metadata": {
                    "dates": ["2023"],
                    "amounts": ["$13,000"],
                    "entities": ["Snyder v. United States"],
                },
                "spacy_metadata": {
                    "entities": {"PERSON": ["Ketanji Brown Jackson"]},
                },
            },
        },
        legs=("semantic",),
    )

    facts = module._facts_context([hit], max_items=10)

    assert "lede_fact: Justice Ketanji Brown Jackson dissented." in facts
    assert "lede_date: 2023" in facts
    assert "lede_amount: $13,000" in facts
    assert "lede_entity.PERSON: Ketanji Brown Jackson" in facts
    assert "heading: Snyder v. United States: Decision" in facts
