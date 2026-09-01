import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parents[3]
SAMPLE = REPO / "docs" / "samples" / "images-keyless.yaml"
README = REPO / "README.md"


def test_sample_yaml_is_valid_config():
    from chunkshop.config import CellConfig

    cfg = CellConfig(**yaml.safe_load(SAMPLE.read_text(encoding="utf-8")))
    assert cfg.source.derive is not None


def test_sample_uses_no_extra_requiring_rung():
    """SC-1: the documented quickstart must run on a base install."""
    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    rungs = {block["rung"] for block in data["source"]["derive"].values()}
    needs_extra = {"ocr", "asr", "vlm_caption", "subprocess"}
    assert not (rungs & needs_extra), f"quickstart uses extras-requiring rungs: {rungs}"


def test_readme_block_matches_the_sample_file():
    """The claim rots the moment the README and the tested file diverge."""
    readme = README.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- BEGIN images-keyless -->\s*```yaml\n(.*?)```", readme, re.S
    )
    assert match, "README must contain a fenced images-keyless block"
    assert match.group(1).strip() == SAMPLE.read_text(encoding="utf-8").strip()
