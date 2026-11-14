"""`chunkshop validate` must work for openai-type embedder cells (they name the
model `model`, not `model_name`), and every cell sample that docs present must validate."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from chunkshop.cli import cli

SAMPLES = Path(__file__).resolve().parents[3] / "docs/samples"


@pytest.mark.parametrize(
    "rel",
    ["sample.yaml", "sample-openai-embedder.yaml", "incremental-files/sample.yaml",
     "bakeoff-ntsb/bakeoff-ntsb-pg.yaml"],
)
def test_documented_samples_validate(rel):
    result = CliRunner().invoke(cli, ["validate", "--config", str(SAMPLES / rel)])
    assert result.exit_code == 0, result.output
