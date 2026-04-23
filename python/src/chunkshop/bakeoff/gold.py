"""Gold-query loader for bakeoff configs (SC-003).

`BakeoffConfig.gold_queries` is `str | list[GoldQuery]` — a path to a YAML/JSON
file on disk, or an inline list already validated by pydantic. This module
resolves either shape to a concrete `list[GoldQuery]`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

import yaml

from chunkshop.bakeoff.config import GoldQuery


def load_gold_queries(spec: Union[str, list[GoldQuery]]) -> list[GoldQuery]:
    """Resolve a `gold_queries` spec to a validated list of `GoldQuery`.

    - `list[GoldQuery]` — returned as-is (already pydantic-validated).
    - `str` — treated as a filesystem path. `.json` parses as JSON array;
      anything else parses as a YAML list. Each element is run through
      `GoldQuery.model_validate`.
    """
    if isinstance(spec, list):
        return spec
    path = Path(spec)
    if not path.exists():
        raise FileNotFoundError(f"gold_queries file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        raw = json.loads(text)
    else:
        raw = yaml.safe_load(text)
    if not isinstance(raw, list):
        raise ValueError(
            f"gold_queries file must be a YAML/JSON list; got {type(raw).__name__}"
        )
    return [GoldQuery.model_validate(x) for x in raw]
