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
target:
  dsn_env: TEST_DSN
  schema: bakeoff_test
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
