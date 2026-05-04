"""Config validation for the bakeoff package (SC-002, SC-003)."""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from chunkshop.bakeoff.config import BakeoffConfig, GoldQuery


MINIMAL_YAML = """
name: test_run
source:
  type: files
  glob: /tmp/*.md
  id_from: stem
gold_queries:
  - {query: "what is x", gold_doc_id: "doc1"}
matrix:
  embedders:
    - {type: fastembed, model_name: Xenova/bge-small-en-v1.5-int8, dim: 384}
  chunkers:
    - {type: hierarchy}
targets:
  - {type: postgres, dsn_env: TEST_DSN, database: bakeoff_test}
"""


def test_minimal_parses():
    cfg = BakeoffConfig.model_validate(yaml.safe_load(MINIMAL_YAML))
    assert cfg.name == "test_run"
    assert len(cfg.matrix.embedders) == 1
    assert len(cfg.matrix.chunkers) == 1
    assert isinstance(cfg.gold_queries, list)
    assert cfg.gold_queries[0].query == "what is x"


def test_empty_matrix_rejected():
    bad = yaml.safe_load(MINIMAL_YAML)
    bad["matrix"]["embedders"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        BakeoffConfig.model_validate(bad)


def test_gold_queries_as_path_string_preserved():
    cfg = yaml.safe_load(MINIMAL_YAML)
    cfg["gold_queries"] = "/path/to/gold.yaml"
    parsed = BakeoffConfig.model_validate(cfg)
    assert parsed.gold_queries == "/path/to/gold.yaml"


def test_unknown_field_forbidden():
    bad = yaml.safe_load(MINIMAL_YAML)
    bad["mystery"] = "nope"
    with pytest.raises(ValidationError, match="Extra"):
        BakeoffConfig.model_validate(bad)


def test_scoring_defaults():
    cfg = BakeoffConfig.model_validate(yaml.safe_load(MINIMAL_YAML))
    assert cfg.scoring.k == [1, 3, 5]
    assert cfg.scoring.include_mrr is True
    assert cfg.scoring.top_k == 5


def test_load_gold_queries_from_yaml_file(tmp_path):
    from chunkshop.bakeoff.gold import load_gold_queries
    p = tmp_path / "gold.yaml"
    p.write_text("- {query: 'q1', gold_doc_id: 'd1'}\n- {query: 'q2', gold_doc_id: 'd2'}\n")
    out = load_gold_queries(str(p))
    assert len(out) == 2
    assert out[0].query == "q1"


def test_load_gold_queries_from_json_file(tmp_path):
    from chunkshop.bakeoff.gold import load_gold_queries
    p = tmp_path / "gold.json"
    p.write_text('[{"query":"q1","gold_doc_id":"d1"}]')
    out = load_gold_queries(str(p))
    assert out[0].gold_doc_id == "d1"


def test_load_gold_queries_passes_through_inline():
    from chunkshop.bakeoff.config import GoldQuery
    from chunkshop.bakeoff.gold import load_gold_queries
    inline = [GoldQuery(query="q1", gold_doc_id="d1")]
    assert load_gold_queries(inline) is inline


def test_bakeoff_results_round_trip():
    from chunkshop.bakeoff.config import BakeoffResults, ComboResult
    results = BakeoffResults(
        run_name="test",
        started_at="2026-04-23",
        corpus_label="samples",
        n_queries=2,
        n_combos=1,
        gold_queries=[{"query": "q", "gold_doc_id": "d1"}],
        combos=[ComboResult(
            backend="postgres",
            chunker_key="hierarchy",
            embedder_key="bge_base",
            chunker_label="hierarchy",
            embedder_label="bge-base",
            table="hierarchy__bge_base",
            ingest_chunks=10,
            ingest_wall_seconds=1.2,
            aggregate={"recall_at_1": 1.0, "mrr": 1.0},
            per_query=[],
        )],
    )
    dumped = results.model_dump_json()
    parsed = BakeoffResults.model_validate_json(dumped)
    assert parsed.run_name == "test"
    assert parsed.combos[0].ingest_chunks == 10
    assert parsed.combos[0].backend == "postgres"


def test_targets_minlength_one():
    """`targets:` must be non-empty."""
    bad = yaml.safe_load(MINIMAL_YAML)
    bad["targets"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        BakeoffConfig.model_validate(bad)


def test_targets_discriminated_union_dispatch():
    """Each target type round-trips to the right pydantic model."""
    cfg = yaml.safe_load(MINIMAL_YAML)
    cfg["targets"] = [
        {"type": "postgres", "dsn_env": "PG_DSN", "database": "db_pg"},
        {"type": "mariadb", "dsn_env": "MD_DSN", "database": "db_md"},
        {"type": "sqlite", "dsn_env": "SQ_PATH", "database": "ignored"},
    ]
    parsed = BakeoffConfig.model_validate(cfg)
    assert [t.type for t in parsed.targets] == ["postgres", "mariadb", "sqlite"]
    assert parsed.targets[0].database_name == "db_pg"
    assert parsed.targets[2].dsn_env == "SQ_PATH"


def test_unknown_target_type_rejected():
    """Bad target.type triggers discriminator validation error at config load."""
    bad = yaml.safe_load(MINIMAL_YAML)
    bad["targets"] = [{"type": "redis", "dsn_env": "X", "database": "y"}]
    with pytest.raises(ValidationError):
        BakeoffConfig.model_validate(bad)
