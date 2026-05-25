"""Bakeoff output writers: results.json, report.md, recommended.yaml (SC-006, SC-007, SC-008)."""
from __future__ import annotations

import yaml

from chunkshop.bakeoff.config import (
    BakeoffConfig,
    BakeoffResults,
    ComboResult,
    GoldQuery,
    MatrixConfig,
    PostgresBakeoffTarget,
)
from chunkshop.bakeoff.output import (
    write_recommended_yaml,
    write_report_md,
    write_results_json,
)
from chunkshop.bakeoff.keys import target_display_keys
from chunkshop.config import (
    CellConfig,
    FastembedEmbedder,
    FilesSource,
    HierarchyChunker,
    IdentityFramerConfig,
)


def _fixture_cfg() -> BakeoffConfig:
    return BakeoffConfig(
        name="fixture",
        source=FilesSource(type="files", glob="/tmp/*.md", id_from="stem"),
        framer=IdentityFramerConfig(),
        gold_queries=[GoldQuery(query="q", gold_doc_id="d1")],
        matrix=MatrixConfig(
            embedders=[FastembedEmbedder(
                type="fastembed",
                model_name="Xenova/bge-base-en-v1.5-int8",
                dim=768,
            )],
            chunkers=[HierarchyChunker(type="hierarchy")],
        ),
        targets=[PostgresBakeoffTarget(
            type="postgres", dsn_env="X", **{"database": "bakeoff_fix"},
        )],
    )


def _fixture_results(run_name: str = "fixture") -> BakeoffResults:
    return BakeoffResults(
        run_name=run_name,
        started_at="2026-04-23",
        corpus_label="samples",
        n_queries=1,
        n_combos=1,
        gold_queries=[{"query": "q", "gold_doc_id": "d1"}],
        combos=[ComboResult(
            backend="postgres",
            chunker_key="hierarchy",
            embedder_key="bge_base_en_v1_5_int8",
            chunker_label="hierarchy",
            embedder_label="Xenova/bge-base-en-v1.5-int8",
            table="hierarchy__bge_base_en_v1_5_int8",
            ingest_chunks=13,
            ingest_wall_seconds=1.1,
            aggregate={"recall_at_1": 1.0, "recall_at_3": 1.0, "recall_at_5": 1.0, "mrr": 1.0},
            per_query=[{
                "query": "q",
                "gold_doc_id": "d1",
                "top_k": [{"doc_id": "d1", "seq_num": 0, "distance": 0.0}],
                "recall_at_1": 1,
                "recall_at_3": 1,
                "recall_at_5": 1,
                "mrr": 1.0,
            }],
        )],
    )


def test_results_json_round_trips(tmp_path):
    r = _fixture_results()
    p = write_results_json(r, tmp_path)
    parsed = BakeoffResults.model_validate_json(p.read_text())
    assert parsed.run_name == r.run_name
    assert parsed.combos[0].ingest_chunks == 13


def test_report_md_has_leaderboard_and_stat_note(tmp_path):
    cfg = _fixture_cfg()
    r = _fixture_results()
    p = write_report_md(cfg, r, tmp_path)
    text = p.read_text()
    # Multi-target report header phrasing
    assert "Cross-target comparison" in text
    assert "postgres leaderboard" in text
    assert "hierarchy" in text
    assert "Statistical power" in text


def test_recommended_yaml_parses_as_cell_config(tmp_path):
    cfg = _fixture_cfg()
    r = _fixture_results()
    p = write_recommended_yaml(cfg, r, tmp_path)
    raw = yaml.safe_load(p.read_text())
    # Strip the comment-only marker field (YAML has no native comment-as-data).
    raw.pop("# NOTE", None)
    CellConfig.model_validate(raw)


def test_target_display_keys_distinguish_pgvector_metrics():
    cfg = _fixture_cfg()
    cfg.targets = [
        PostgresBakeoffTarget(
            type="postgres",
            dsn_env="X",
            vector_metric=metric,
            **{"database": "bakeoff_fix"},
        )
        for metric in ("cosine", "inner_product", "l2")
    ]
    assert target_display_keys(cfg.targets) == [
        "postgres_cosine",
        "postgres_inner_product",
        "postgres_l2",
    ]


def test_report_and_recommended_preserve_pgvector_metric_winner(tmp_path):
    cfg = _fixture_cfg()
    cfg.targets = [
        PostgresBakeoffTarget(
            type="postgres",
            dsn_env="X",
            vector_metric=metric,
            **{"database": "bakeoff_fix"},
        )
        for metric in ("cosine", "inner_product", "l2")
    ]
    combos = []
    for target_key, mrr in [
        ("postgres_cosine", 0.0),
        ("postgres_inner_product", 1.0),
        ("postgres_l2", 0.0),
    ]:
        combos.append(ComboResult(
            backend="postgres",
            target_key=target_key,
            chunker_key="hierarchy",
            embedder_key="bge_base_en_v1_5_int8",
            chunker_label="hierarchy",
            embedder_label="Xenova/bge-base-en-v1.5-int8",
            table="hierarchy__bge_base_en_v1_5_int8",
            ingest_chunks=13,
            ingest_wall_seconds=1.1,
            aggregate={
                "recall_at_1": mrr,
                "recall_at_3": mrr,
                "recall_at_5": mrr,
                "mrr": mrr,
            },
            per_query=[],
        ))
    r = BakeoffResults(
        run_name="fixture",
        started_at="2026-04-23",
        corpus_label="samples",
        n_queries=1,
        n_combos=3,
        gold_queries=[{"query": "q", "gold_doc_id": "d1"}],
        combos=combos,
    )

    report = write_report_md(cfg, r, tmp_path).read_text()
    assert "postgres_cosine" in report
    assert "postgres_inner_product" in report
    assert "postgres_l2" in report

    raw = yaml.safe_load(write_recommended_yaml(cfg, r, tmp_path).read_text())
    assert raw["target"]["vector_metric"] == "inner_product"
