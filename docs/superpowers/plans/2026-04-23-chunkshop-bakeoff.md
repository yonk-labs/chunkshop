# chunkshop Bakeoff CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a first-class `chunkshop bakeoff` CLI subcommand that takes a YAML config + gold-queries file and runs every chunker × embedder combo against a user's corpus, scoring with recall@k + MRR, and emitting a leaderboard + `recommended.yaml` ready to drive real ingest.

**Architecture:** New `python/src/chunkshop/bakeoff/` package. Core library code (config models, runner, scoring, output writers) split into focused modules. CLI subcommand wires into the existing `click` CLI group in `cli.py`. Reuses `runner.run_cell`, the existing source/framer/chunker/embedder discriminated unions, and the sink's advisory-locked schema creation. Each combo writes to its own table under a dedicated bakeoff schema.

**Tech Stack:** Python 3.12, pydantic v2, `click` (already the CLI framework), pytest, `pyyaml`.

**Mission brief:** `skill-output/mission-brief/Mission-Brief-bakeoff.md`. This plan implements SC-001…SC-013.

---

## Prerequisites

- All prior briefs merged: schema-flex, DocFramer, metadata extractors, chunker max_chars hotfix. Default embedder is `Xenova/bge-base-en-v1.5-int8`.
- Test baseline: 93 tests passing.
- `scripts/bench_matrix.py` exists and works — treat it as a reference, not a substrate. The CLI must ship as library code.

## File Structure

**New files:**

- `python/src/chunkshop/bakeoff/__init__.py` — re-exports.
- `python/src/chunkshop/bakeoff/config.py` — `BakeoffConfig`, `GoldQuery`, `MatrixConfig`, `BakeoffTargetConfig`, `ScoringConfig`, `BakeoffResults` pydantic models.
- `python/src/chunkshop/bakeoff/keys.py` — deterministic chunker_key / embedder_key derivation for table naming.
- `python/src/chunkshop/bakeoff/gold.py` — load gold-queries from YAML/JSON file or inline list.
- `python/src/chunkshop/bakeoff/score.py` — pure scoring functions (recall@k, MRR) against ranked doc_id lists.
- `python/src/chunkshop/bakeoff/runner.py` — `run_bakeoff(cfg: BakeoffConfig) -> BakeoffResults`.
- `python/src/chunkshop/bakeoff/output.py` — `write_results_json`, `write_report_md`, `write_recommended_yaml`.
- `python/tests/chunkshop/test_bakeoff_config.py` — pydantic round-trips + validation errors.
- `python/tests/chunkshop/test_bakeoff_scoring.py` — pure math tests.
- `python/tests/chunkshop/test_bakeoff_keys.py` — key-derivation tests.
- `python/tests/chunkshop/test_bakeoff_output.py` — output-shape tests (no DB).
- `python/tests/chunkshop/test_bakeoff_e2e.py` — end-to-end against a real Postgres (skips if unreachable).
- `docs/samples/bakeoff.yaml` — runnable 3×2 bakeoff config.
- `docs/samples/bakeoff-gold.yaml` — 10+ hand-written gold queries.
- `docs/tutorial-bakeoff.md` — narrative walkthrough.
- `docs/quickstart-bakeoff.md` — recipe card.

**Modified files:**

- `python/src/chunkshop/cli.py` — register the new `bakeoff` command.
- `README.md` — add a row in the Documentation table for `tutorial-bakeoff.md` and `quickstart-bakeoff.md`.
- `CHANGELOG.md` — "Added" entry for the bakeoff subcommand.

---

## Task 1: Context check + baseline

- [ ] **Step 1:** `cd python && uv run pytest -q` — expect 93 passing. Note exact count; we must preserve it.
- [ ] **Step 2:** Read `python/src/chunkshop/cli.py` to see the click group pattern. Read `scripts/bench_matrix.py` to understand the combo-loop shape you'll be reimplementing as library code.
- [ ] **Step 3:** Read `python/src/chunkshop/config.py` — you'll be adding new pydantic models that reuse existing unions (`SourceConfig`, `FramerConfig`, `ChunkerConfig`, `FastembedEmbedder`).

## Task 2: Pydantic config models (SC-002, SC-003)

**Files:**
- Create: `python/src/chunkshop/bakeoff/__init__.py`
- Create: `python/src/chunkshop/bakeoff/config.py`

- [ ] **Step 1: Write failing test first**

Create `python/tests/chunkshop/test_bakeoff_config.py`:

```python
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
```

Run: expect ImportError.

- [ ] **Step 2: Implement `BakeoffConfig` + supporting models**

Create `python/src/chunkshop/bakeoff/config.py`. Key shapes:

```python
from __future__ import annotations
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chunkshop.config import (
    ChunkerConfig,
    FastembedEmbedder,
    FramerConfig,
    IdentityFramerConfig,
    RuntimeConfig,
    SourceConfig,
)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldQuery(_Base):
    query: str
    gold_doc_id: str


class MatrixConfig(_Base):
    embedders: list[FastembedEmbedder] = Field(..., min_length=1)
    chunkers: list[ChunkerConfig] = Field(..., min_length=1)


class BakeoffTargetConfig(_Base):
    dsn_env: str
    schema_name: str = Field(alias="schema")


class ScoringConfig(_Base):
    k: list[int] = [1, 3, 5]
    include_mrr: bool = True
    top_k: int = 5

    @field_validator("k")
    @classmethod
    def _ks_positive(cls, v: list[int]) -> list[int]:
        if not v or any(k <= 0 for k in v):
            raise ValueError("scoring.k must be a non-empty list of positive ints")
        return sorted(set(v))


class BakeoffConfig(_Base):
    name: str
    source: SourceConfig
    framer: FramerConfig = Field(default_factory=IdentityFramerConfig)
    gold_queries: Union[str, list[GoldQuery]]  # path OR inline
    matrix: MatrixConfig
    target: BakeoffTargetConfig
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    output_dir: Optional[str] = None
    runtime: Optional[RuntimeConfig] = None
```

- [ ] **Step 3: Create `__init__.py` that re-exports**

```python
from chunkshop.bakeoff.config import (
    BakeoffConfig,
    BakeoffTargetConfig,
    GoldQuery,
    MatrixConfig,
    ScoringConfig,
)

__all__ = [
    "BakeoffConfig",
    "BakeoffTargetConfig",
    "GoldQuery",
    "MatrixConfig",
    "ScoringConfig",
]
```

- [ ] **Step 4: Run tests**

```bash
cd python && uv run pytest tests/chunkshop/test_bakeoff_config.py -q
```

Expect 5 passing.

- [ ] **Step 5: Commit**

```bash
git add python/src/chunkshop/bakeoff/__init__.py python/src/chunkshop/bakeoff/config.py python/tests/chunkshop/test_bakeoff_config.py
git commit -m "feat(bakeoff): pydantic config models + schema validation (SC-002, SC-003)"
```

## ⛔ DC-001 Drift Check

- `BakeoffConfig.model_validate` succeeds on a minimal YAML.
- Empty embedders / chunkers list raises validation error.
- `extra="forbid"` catches unknown fields.
- Default scoring config is `{k: [1,3,5], include_mrr: True, top_k: 5}`.

## Task 3: Deterministic key derivation (SC-004)

**Files:**
- Create: `python/src/chunkshop/bakeoff/keys.py`
- Create: `python/tests/chunkshop/test_bakeoff_keys.py`

- [ ] **Step 1: Write failing test**

```python
from chunkshop.bakeoff.keys import chunker_key, embedder_key, combo_table
from chunkshop.config import (
    FastembedEmbedder, FixedOverlapChunker, HierarchyChunker,
    NeighborExpandChunker, SentenceAwareChunker,
)


def test_embedder_key_strips_org_and_punctuation():
    e = FastembedEmbedder(type="fastembed", model_name="Xenova/bge-base-en-v1.5-int8", dim=768)
    assert embedder_key(e) == "bge_base_en_v1_5_int8"


def test_chunker_key_simple():
    assert chunker_key(HierarchyChunker(type="hierarchy")) == "hierarchy"
    assert chunker_key(SentenceAwareChunker(type="sentence_aware")) == "sentence_aware"


def test_chunker_key_fixed_overlap_includes_window():
    c = FixedOverlapChunker(type="fixed_overlap", window_words=300, step_words=150)
    assert chunker_key(c) == "fixed_overlap_w300_s150"


def test_chunker_key_neighbor_expand_includes_base():
    c = NeighborExpandChunker(
        type="neighbor_expand",
        base=SentenceAwareChunker(type="sentence_aware"),
        window=1,
    )
    assert chunker_key(c) == "neighbor_expand_w1_over_sentence_aware"


def test_combo_table_joins_keys():
    e = FastembedEmbedder(type="fastembed", model_name="Xenova/bge-small-en-v1.5-int8", dim=384)
    c = HierarchyChunker(type="hierarchy")
    assert combo_table(c, e) == "hierarchy__bge_small_en_v1_5_int8"
```

- [ ] **Step 2: Implement**

```python
# python/src/chunkshop/bakeoff/keys.py
import re
from chunkshop.config import (
    ChunkerConfig, FastembedEmbedder, FixedOverlapChunker,
    HierarchyChunker, NeighborExpandChunker, SentenceAwareChunker,
)

_ID_SAFE = re.compile(r"[^a-z0-9]+")


def embedder_key(cfg: FastembedEmbedder) -> str:
    short = cfg.model_name.split("/")[-1].lower()
    return _ID_SAFE.sub("_", short).strip("_")


def chunker_key(cfg: ChunkerConfig) -> str:
    if isinstance(cfg, HierarchyChunker):
        return "hierarchy"
    if isinstance(cfg, SentenceAwareChunker):
        return "sentence_aware"
    if isinstance(cfg, FixedOverlapChunker):
        return f"fixed_overlap_w{cfg.window_words}_s{cfg.step_words}"
    if isinstance(cfg, NeighborExpandChunker):
        return f"neighbor_expand_w{cfg.window}_over_{chunker_key(cfg.base)}"
    raise ValueError(f"unknown chunker type for key derivation: {type(cfg).__name__}")


def combo_table(chunker: ChunkerConfig, embedder: FastembedEmbedder) -> str:
    return f"{chunker_key(chunker)}__{embedder_key(embedder)}"
```

- [ ] **Step 3: Run tests + commit**

```bash
cd python && uv run pytest tests/chunkshop/test_bakeoff_keys.py -q
git add python/src/chunkshop/bakeoff/keys.py python/tests/chunkshop/test_bakeoff_keys.py
git commit -m "feat(bakeoff): deterministic chunker + embedder key derivation for combo tables (SC-004)"
```

## Task 4: Gold-query loader (SC-003)

**Files:**
- Create: `python/src/chunkshop/bakeoff/gold.py`

- [ ] **Step 1: Implement**

```python
# python/src/chunkshop/bakeoff/gold.py
import json
from pathlib import Path
from typing import Union

import yaml

from chunkshop.bakeoff.config import GoldQuery


def load_gold_queries(spec: Union[str, list[GoldQuery]]) -> list[GoldQuery]:
    """Resolve `BakeoffConfig.gold_queries` to a concrete list.

    If `spec` is a string, treat as a filesystem path to a YAML or JSON file
    holding a list of `{query, gold_doc_id}` objects. Otherwise return as-is.
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
        raise ValueError(f"gold_queries file must be a YAML/JSON list; got {type(raw).__name__}")
    return [GoldQuery.model_validate(x) for x in raw]
```

- [ ] **Step 2: Inline test at the bottom of `test_bakeoff_config.py`**

```python
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


def test_load_gold_queries_passes_through_inline(tmp_path):
    from chunkshop.bakeoff.config import GoldQuery
    from chunkshop.bakeoff.gold import load_gold_queries
    inline = [GoldQuery(query="q1", gold_doc_id="d1")]
    assert load_gold_queries(inline) is inline
```

- [ ] **Step 3: Run + commit**

```bash
cd python && uv run pytest tests/chunkshop/test_bakeoff_config.py -q
git add python/src/chunkshop/bakeoff/gold.py python/tests/chunkshop/test_bakeoff_config.py
git commit -m "feat(bakeoff): gold-query loader supporting YAML, JSON, and inline lists (SC-003)"
```

## Task 5: Scoring functions (SC-005)

**Files:**
- Create: `python/src/chunkshop/bakeoff/score.py`
- Create: `python/tests/chunkshop/test_bakeoff_scoring.py`

- [ ] **Step 1: Write failing tests**

```python
from chunkshop.bakeoff.score import score_query, aggregate_scores


def test_score_gold_at_rank_1():
    s = score_query(ranked_doc_ids=["d1", "d2", "d3"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert s["recall_at_1"] == 1
    assert s["recall_at_3"] == 1
    assert s["recall_at_5"] == 1
    assert s["mrr"] == 1.0


def test_score_gold_at_rank_3():
    s = score_query(ranked_doc_ids=["d2", "d3", "d1", "d4", "d5"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert s["recall_at_1"] == 0
    assert s["recall_at_3"] == 1
    assert s["recall_at_5"] == 1
    assert abs(s["mrr"] - 1/3) < 1e-9


def test_score_gold_absent():
    s = score_query(ranked_doc_ids=["d9", "d8", "d7"], gold_doc_id="d1", k_values=[1, 3, 5])
    assert all(s[f"recall_at_{k}"] == 0 for k in [1, 3, 5])
    assert s["mrr"] == 0.0


def test_score_empty_top_k():
    s = score_query(ranked_doc_ids=[], gold_doc_id="d1", k_values=[1, 3, 5])
    assert all(s[f"recall_at_{k}"] == 0 for k in [1, 3, 5])
    assert s["mrr"] == 0.0


def test_aggregate_mean_across_queries():
    per_query = [
        {"recall_at_1": 1, "recall_at_3": 1, "mrr": 1.0},
        {"recall_at_1": 0, "recall_at_3": 1, "mrr": 0.5},
        {"recall_at_1": 0, "recall_at_3": 0, "mrr": 0.0},
    ]
    agg = aggregate_scores(per_query)
    assert abs(agg["recall_at_1"] - 1/3) < 1e-9
    assert abs(agg["recall_at_3"] - 2/3) < 1e-9
    assert abs(agg["mrr"] - 0.5) < 1e-9
```

- [ ] **Step 2: Implement**

```python
# python/src/chunkshop/bakeoff/score.py
from typing import Iterable


def score_query(
    ranked_doc_ids: list[str],
    gold_doc_id: str,
    k_values: Iterable[int],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for k in k_values:
        result[f"recall_at_{k}"] = 1 if gold_doc_id in ranked_doc_ids[:k] else 0
    mrr = 0.0
    for rank, did in enumerate(ranked_doc_ids, start=1):
        if did == gold_doc_id:
            mrr = 1.0 / rank
            break
    result["mrr"] = mrr
    return result


def aggregate_scores(per_query: list[dict[str, float]]) -> dict[str, float]:
    if not per_query:
        return {}
    n = len(per_query)
    keys = per_query[0].keys()
    return {k: sum(q[k] for q in per_query) / n for k in keys}
```

- [ ] **Step 3: Run + commit**

```bash
cd python && uv run pytest tests/chunkshop/test_bakeoff_scoring.py -q
git add python/src/chunkshop/bakeoff/score.py python/tests/chunkshop/test_bakeoff_scoring.py
git commit -m "feat(bakeoff): pure scoring functions — recall@k + MRR + aggregation (SC-005)"
```

## ⛔ DC-003 Drift Check

- Gold at rank 1 → recall@{1,3,5}=1, MRR=1.0
- Gold at rank 3 → recall@1=0, recall@3=1, MRR≈0.333
- Gold absent / empty top-K → all 0 (no crash)
- Aggregate = mean across queries

## Task 6: Core bakeoff runner (SC-004, SC-005, SC-006)

**Files:**
- Create: `python/src/chunkshop/bakeoff/runner.py`

- [ ] **Step 1: Implement `run_bakeoff`**

Shape:

```python
# python/src/chunkshop/bakeoff/runner.py
from __future__ import annotations
import os
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import psycopg
from psycopg import sql

from chunkshop.bakeoff.config import BakeoffConfig
from chunkshop.bakeoff.gold import load_gold_queries
from chunkshop.bakeoff.keys import chunker_key, combo_table, embedder_key
from chunkshop.bakeoff.score import aggregate_scores, score_query
from chunkshop.config import CellConfig, TargetConfig
from chunkshop.embedders import load_embedder
from chunkshop.runner import run_cell


@dataclass
class ComboResult:
    chunker_key: str
    embedder_key: str
    chunker_label: str        # human-readable (e.g. "hierarchy", "neighbor_expand(window=1, base=hierarchy)")
    embedder_label: str       # e.g. "Xenova/bge-base-en-v1.5-int8"
    table: str
    ingest_chunks: int
    ingest_wall_seconds: float
    aggregate: dict[str, float]      # mean recall@k + MRR
    per_query: list[dict[str, Any]]  # [{query, gold_doc_id, top_k: [doc_ids], recall_at_k, mrr}, ...]


@dataclass
class BakeoffResults:
    run_name: str
    started_at: str
    corpus_label: str
    n_queries: int
    n_combos: int
    combos: list[ComboResult]
    gold_queries: list[dict[str, str]]


def run_bakeoff(cfg: BakeoffConfig) -> BakeoffResults:
    # 1. Resolve gold queries (file or inline) → list[GoldQuery]
    # 2. Tell the sink where to connect: `os.environ[cfg.target.dsn_env] = ...` already set by caller
    #    (CLI does this). Assert the env var is set.
    # 3. Build every CellConfig from the cross-product of matrix.embedders × matrix.chunkers.
    # 4. Serial ingest: for each combo, build the CellConfig, call run_cell(), count chunks.
    # 5. For each embedder, load the model once and embed all gold queries in a batch (cached).
    # 6. For each combo, run pgvector top-K against that combo's table, score per query, aggregate.
    # 7. Return BakeoffResults with per-combo data.
    ...
```

Key details:

- **Schema creation** happens naturally via `run_cell` → `sink.create_table()` → advisory lock (already fixed). Don't pre-create the schema.
- **Serial loop** over combos — not parallel. MVP.
- **Pre-flight confirmation** for matrices > 50 cells: `if len(embedders) * len(chunkers) > 50: prompt user` (the CLI layer handles the prompt; runner just computes the count and surfaces it in a raised `ValueError` if a `--yes` flag wasn't supplied — wire this via CLI in Task 8).
- **DSN env var**: the CLI sets `os.environ[cfg.target.dsn_env] = <dsn from args>` before calling. Runner asserts presence.
- **Cell name**: `{cfg.name}__{chunker_key}__{embedder_key}` — matches the log path convention.

- [ ] **Step 2: Wire `ComboResult` + `BakeoffResults` to pydantic for round-trip**

Actually these are dataclasses. Either keep as dataclasses and serialize via `dataclasses.asdict()`, or promote to pydantic for validation. Recommendation: **pydantic BaseModels** — matches the rest of the codebase and gives free JSON round-trip. Move these to `bakeoff/config.py` alongside `BakeoffConfig`.

- [ ] **Step 3: Lightweight integration test (no DB)**

Add to `test_bakeoff_config.py`:

```python
def test_bakeoff_results_round_trip():
    from chunkshop.bakeoff.config import BakeoffResults, ComboResult
    results = BakeoffResults(
        run_name="test", started_at="2026-04-23", corpus_label="samples",
        n_queries=2, n_combos=1, gold_queries=[{"query": "q", "gold_doc_id": "d1"}],
        combos=[ComboResult(
            chunker_key="hierarchy", embedder_key="bge_base",
            chunker_label="hierarchy", embedder_label="bge-base",
            table="hierarchy__bge_base", ingest_chunks=10, ingest_wall_seconds=1.2,
            aggregate={"recall_at_1": 1.0, "mrr": 1.0}, per_query=[],
        )],
    )
    dumped = results.model_dump_json()
    BakeoffResults.model_validate_json(dumped)  # round-trip
```

- [ ] **Step 4: Commit**

```bash
git add python/src/chunkshop/bakeoff/runner.py python/src/chunkshop/bakeoff/config.py python/tests/chunkshop/test_bakeoff_config.py
git commit -m "feat(bakeoff): run_bakeoff() — ingest every combo, score against gold, aggregate (SC-004, SC-005, SC-006)"
```

## ⛔ DC-002 Drift Check

- `run_bakeoff` signature is `(cfg: BakeoffConfig) -> BakeoffResults`.
- Schema created per sink's existing advisory-locked path; no new DDL in bakeoff code.
- DSN env is read, not passed — caller (CLI) is responsible for setting it.
- Matrix size > 50 raises `ValueError` unless caller sets a bypass (pattern: `cfg.matrix.allow_large = True` OR CLI `--yes`). Go with CLI `--yes` for cleanliness.

## Task 7: Output writers (SC-006, SC-007, SC-008)

**Files:**
- Create: `python/src/chunkshop/bakeoff/output.py`
- Create: `python/tests/chunkshop/test_bakeoff_output.py`

- [ ] **Step 1: Implement three writers**

```python
# python/src/chunkshop/bakeoff/output.py
from __future__ import annotations
import json
from pathlib import Path

import yaml

from chunkshop.bakeoff.config import BakeoffConfig, BakeoffResults


def write_results_json(results: BakeoffResults, out_dir: Path) -> Path:
    out = out_dir / "results.json"
    out.write_text(results.model_dump_json(indent=2))
    return out


def write_report_md(
    cfg: BakeoffConfig, results: BakeoffResults, out_dir: Path
) -> Path:
    # Sort combos by MRR descending
    ranked = sorted(results.combos, key=lambda c: -c.aggregate.get("mrr", 0))

    # Header
    lines = [
        f"# Bakeoff report: {results.run_name}",
        "",
        f"- Run: {results.started_at}",
        f"- Corpus: {results.corpus_label}",
        f"- Queries: {results.n_queries}",
        f"- Combos: {results.n_combos}",
        "",
        "## Leaderboard (sorted by MRR)",
        "",
        "| # | Chunker | Embedder | " + " | ".join(f"r@{k}" for k in cfg.scoring.k) + " | MRR |",
        "|---|---------|----------|" + "|".join(["---"] * (len(cfg.scoring.k) + 1)) + "|",
    ]
    for i, c in enumerate(ranked, start=1):
        rk = [f"{c.aggregate.get(f'recall_at_{k}', 0):.3f}" for k in cfg.scoring.k]
        mrr = f"{c.aggregate.get('mrr', 0):.3f}"
        lines.append(f"| {i} | `{c.chunker_label}` | `{c.embedder_label}` | " + " | ".join(rk) + f" | {mrr} |")

    # Honesty note scaled to n_queries
    lines += [
        "",
        "## Statistical power",
        "",
        f"{results.n_queries} queries means one query flipping moves aggregate recall by "
        f"{1/results.n_queries:.3f}. Combos within ~{2/results.n_queries:.2f} of the leader "
        "are not reliably distinguishable. Re-run with more queries or a larger corpus "
        "before treating the leaderboard as a tournament result.",
        "",
    ]
    out = out_dir / "report.md"
    out.write_text("\n".join(lines))
    return out


def write_recommended_yaml(
    cfg: BakeoffConfig, results: BakeoffResults, out_dir: Path
) -> Path:
    ranked = sorted(results.combos, key=lambda c: -c.aggregate.get("mrr", 0))
    top = ranked[0]

    # Find the original chunker + embedder configs by key
    winner_chunker = next(
        c for c in cfg.matrix.chunkers
        if __import__("chunkshop.bakeoff.keys", fromlist=["chunker_key"]).chunker_key(c) == top.chunker_key
    )
    winner_embedder = next(
        e for e in cfg.matrix.embedders
        if __import__("chunkshop.bakeoff.keys", fromlist=["embedder_key"]).embedder_key(e) == top.embedder_key
    )

    recommended = {
        "# NOTE": f"Top combo from bakeoff '{results.run_name}' (MRR={top.aggregate.get('mrr', 0):.3f}, r@1={top.aggregate.get('recall_at_1', 0):.3f}).",
        "cell_name": f"{results.run_name}_recommended",
        "source": cfg.source.model_dump(exclude_none=True),
        "framer": cfg.framer.model_dump(exclude_none=True),
        "chunker": winner_chunker.model_dump(exclude_none=True),
        "embedder": winner_embedder.model_dump(exclude_none=True),
        "target": {
            "dsn_env": cfg.target.dsn_env,
            "schema": cfg.target.schema_name,
            "table": f"{results.run_name}_production",
            "mode": "overwrite",
        },
    }
    out = out_dir / "recommended.yaml"
    out.write_text(yaml.safe_dump(recommended, sort_keys=False))
    return out
```

- [ ] **Step 2: Tests (no DB)**

```python
# python/tests/chunkshop/test_bakeoff_output.py
import yaml
from pathlib import Path

from chunkshop.bakeoff.config import (
    BakeoffConfig, BakeoffResults, ComboResult, GoldQuery, MatrixConfig,
    BakeoffTargetConfig, ScoringConfig,
)
from chunkshop.bakeoff.output import write_report_md, write_recommended_yaml, write_results_json
from chunkshop.config import CellConfig, FastembedEmbedder, FilesSource, HierarchyChunker, IdentityFramerConfig


def _fixture_cfg() -> BakeoffConfig:
    return BakeoffConfig(
        name="fixture",
        source=FilesSource(type="files", glob="/tmp/*.md", id_from="stem"),
        framer=IdentityFramerConfig(),
        gold_queries=[GoldQuery(query="q", gold_doc_id="d1")],
        matrix=MatrixConfig(
            embedders=[FastembedEmbedder(type="fastembed", model_name="Xenova/bge-base-en-v1.5-int8", dim=768)],
            chunkers=[HierarchyChunker(type="hierarchy")],
        ),
        target=BakeoffTargetConfig(dsn_env="X", **{"schema": "bakeoff_fix"}),
    )


def _fixture_results(run_name="fixture") -> BakeoffResults:
    return BakeoffResults(
        run_name=run_name, started_at="2026-04-23", corpus_label="samples",
        n_queries=1, n_combos=1,
        gold_queries=[{"query": "q", "gold_doc_id": "d1"}],
        combos=[ComboResult(
            chunker_key="hierarchy", embedder_key="bge_base_en_v1_5_int8",
            chunker_label="hierarchy",
            embedder_label="Xenova/bge-base-en-v1.5-int8",
            table="hierarchy__bge_base_en_v1_5_int8",
            ingest_chunks=13, ingest_wall_seconds=1.1,
            aggregate={"recall_at_1": 1.0, "recall_at_3": 1.0, "recall_at_5": 1.0, "mrr": 1.0},
            per_query=[],
        )],
    )


def test_results_json_round_trips(tmp_path):
    r = _fixture_results()
    p = write_results_json(r, tmp_path)
    parsed = BakeoffResults.model_validate_json(p.read_text())
    assert parsed.run_name == r.run_name


def test_report_md_has_leaderboard_and_stat_note(tmp_path):
    cfg = _fixture_cfg()
    r = _fixture_results()
    p = write_report_md(cfg, r, tmp_path)
    text = p.read_text()
    assert "Leaderboard" in text
    assert "hierarchy" in text
    assert "Statistical power" in text


def test_recommended_yaml_parses_as_cell_config(tmp_path):
    cfg = _fixture_cfg()
    r = _fixture_results()
    p = write_recommended_yaml(cfg, r, tmp_path)
    raw = yaml.safe_load(p.read_text())
    # Strip the comment-only marker field
    raw.pop("# NOTE", None)
    CellConfig.model_validate(raw)
```

- [ ] **Step 3: Run + commit**

```bash
cd python && uv run pytest tests/chunkshop/test_bakeoff_output.py -q
git add python/src/chunkshop/bakeoff/output.py python/tests/chunkshop/test_bakeoff_output.py
git commit -m "feat(bakeoff): output writers — results.json, leaderboard report.md, recommended.yaml (SC-006, SC-007, SC-008)"
```

## ⛔ DC-004 Drift Check

- `results.json` round-trips back to `BakeoffResults`.
- `report.md` contains a leaderboard table, a statistical-power note.
- `recommended.yaml` parses as a valid `CellConfig`.

## Task 8: CLI wiring (SC-001)

**Files:**
- Modify: `python/src/chunkshop/cli.py`

- [ ] **Step 1: Register the `bakeoff` command**

```python
@cli.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--dsn", envvar="CHUNKSHOP_DSN", required=True, help="Postgres DSN (or $CHUNKSHOP_DSN)")
@click.option("--yes", is_flag=True, help="Bypass the >50-cell matrix confirmation prompt")
@click.option("--keep-schema", is_flag=True, help="Do not drop the bakeoff schema on exit")
def bakeoff(config_path: str, dsn: str, yes: bool, keep_schema: bool):
    """Run a chunker × embedder matrix bakeoff against a corpus.

    The config file names the corpus, the gold queries, the combo matrix, and
    the target DB. chunkshop ingests every combo into its own table, embeds
    each gold query with each embedder, scores recall@k + MRR per combo, and
    writes a leaderboard + recommended cell YAML to the output dir.
    """
    import yaml
    from pathlib import Path
    from chunkshop.bakeoff.config import BakeoffConfig
    from chunkshop.bakeoff.runner import run_bakeoff
    from chunkshop.bakeoff.output import write_results_json, write_report_md, write_recommended_yaml

    cfg = BakeoffConfig.model_validate(yaml.safe_load(Path(config_path).read_text()))

    n_combos = len(cfg.matrix.embedders) * len(cfg.matrix.chunkers)
    if n_combos > 50 and not yes:
        click.echo(f"WARNING: {n_combos} combos is large ({len(cfg.matrix.embedders)} embedders × {len(cfg.matrix.chunkers)} chunkers)")
        if not click.confirm("Proceed?", default=False):
            click.echo("Aborted")
            raise click.Abort()

    os.environ[cfg.target.dsn_env] = dsn
    results = run_bakeoff(cfg)

    out_dir = Path(cfg.output_dir or f"skill-output/bakeoff/{cfg.name}")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = write_results_json(results, out_dir)
    md_path = write_report_md(cfg, results, out_dir)
    yaml_path = write_recommended_yaml(cfg, results, out_dir)

    if not keep_schema:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(cfg.target.schema_name)))

    click.echo(f"\nResults: {json_path}")
    click.echo(f"Report:  {md_path}")
    click.echo(f"Recommended cell: {yaml_path}")
```

- [ ] **Step 2: Smoke-test via help**

```bash
cd python && uv run chunkshop bakeoff --help
```

Expect: help text includes `--config`, `--dsn`, `--yes`, `--keep-schema`.

- [ ] **Step 3: Commit**

```bash
git add python/src/chunkshop/cli.py
git commit -m "feat(bakeoff): chunkshop bakeoff CLI subcommand — config-driven matrix evaluation (SC-001)"
```

## Task 9: Sample files (SC-009)

**Files:**
- Create: `docs/samples/bakeoff.yaml`
- Create: `docs/samples/bakeoff-gold.yaml`

- [ ] **Step 1: `docs/samples/bakeoff.yaml`**

```yaml
# Runnable bakeoff config against docs/samples/*-*.md.
# Usage: chunkshop bakeoff --config docs/samples/bakeoff.yaml

name: samples_bakeoff

source:
  type: files
  glob: docs/samples/*-*.md
  id_from: stem

framer:
  type: identity

gold_queries: docs/samples/bakeoff-gold.yaml

matrix:
  embedders:
    - {type: fastembed, model_name: Xenova/bge-small-en-v1.5-int8, dim: 384}
    - {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    - {type: fastembed, model_name: nomic-ai/nomic-embed-text-v1.5-Q, dim: 768}
  chunkers:
    - {type: hierarchy}
    - {type: sentence_aware}

target:
  dsn_env: CHUNKSHOP_DSN
  schema: chunkshop_bakeoff_samples

scoring:
  k: [1, 3, 5]
  include_mrr: true
  top_k: 5
```

- [ ] **Step 2: `docs/samples/bakeoff-gold.yaml`**

Reuse the 14 gold queries from `scripts/bench_matrix.py` (copy the text, drop gold_section field — MVP is doc-id-level).

- [ ] **Step 3: Commit**

```bash
git add docs/samples/bakeoff.yaml docs/samples/bakeoff-gold.yaml
git commit -m "docs(samples): runnable bakeoff config + 14 gold queries against samples corpus (SC-009)"
```

## Task 10: E2E test (SC-013)

**Files:**
- Create: `python/tests/chunkshop/test_bakeoff_e2e.py`

- [ ] **Step 1: Write the test**

Mirror `test_end_to_end_samples_corpus.py` pattern: check PG reachability → skip if not, otherwise run a minimal 2×2 bakeoff using `chunkshop.bakeoff.runner.run_bakeoff` directly, assert the leaderboard has 4 combos, each with `aggregate.recall_at_1` populated, winner is `hierarchy + bge-base-int8`.

- [ ] **Step 2: Run + commit**

```bash
cd python && uv run pytest tests/chunkshop/test_bakeoff_e2e.py -q
git add python/tests/chunkshop/test_bakeoff_e2e.py
git commit -m "test(bakeoff): e2e — 2x2 matrix against samples corpus asserts leaderboard shape (SC-013)"
```

## ⛔ DC-005 Drift Check

- E2E test passes against real Postgres.
- `chunkshop bakeoff --help` shows all flags.
- Running the subcommand against `docs/samples/bakeoff.yaml` produces a real leaderboard.

## Task 11: Tutorial (SC-010)

**Files:**
- Create: `docs/tutorial-bakeoff.md`

- [ ] **Step 1: Write**

Structure:
- **Why a bakeoff** — the "which combo for MY docs?" problem.
- **Setup** — you need Postgres + a corpus + 10+ gold queries.
- **Step 1: Author gold queries** — walk through writing 10 queries against a fictional corpus; honest about the 14-query statistical-power floor.
- **Step 2: Author the bakeoff config** — explain each section; show the matrix.
- **Step 3: Run `chunkshop bakeoff --config ...`** — expected output, timing.
- **Step 4: Read the report** — leaderboard interpretation, honesty note.
- **Step 5: Use the `recommended.yaml`** — run `chunkshop ingest` with it against your real corpus, check output table.

- [ ] **Step 2: Commit**

```bash
git add docs/tutorial-bakeoff.md
git commit -m "docs(tutorial): bakeoff walkthrough — gold queries, config, run, interpret (SC-010)"
```

## Task 12: Quickstart + docs-index updates (SC-011)

**Files:**
- Create: `docs/quickstart-bakeoff.md`
- Modify: `README.md` — add rows to the Documentation table
- Modify: `CHANGELOG.md` — "Added" entry

- [ ] **Step 1: `docs/quickstart-bakeoff.md`**

Recipe card: decision tree ("I want to compare just embedders" → fix chunkers list to one entry; "I want to find the best chunker" → fix embedders list to one entry). Copy-paste YAML recipes.

- [ ] **Step 2: README row**

Under Documentation, add:
```
| [`docs/tutorial-bakeoff.md`](docs/tutorial-bakeoff.md) | Bakeoff walkthrough: pick the best chunker+embedder for your corpus. |
| [`docs/quickstart-bakeoff.md`](docs/quickstart-bakeoff.md) | Recipe card: common bakeoff shapes (embedder-only, chunker-only, full factorial). |
```

- [ ] **Step 3: CHANGELOG**

Under "Unreleased > Added":
```
- **`chunkshop bakeoff` CLI.** Config-driven chunker × embedder matrix
  evaluation against a user's corpus. Outputs a leaderboard + a
  `recommended.yaml` that's a runnable `chunkshop ingest` cell pre-filled
  with the top-MRR combo. Config schema in `python/src/chunkshop/bakeoff/
  config.py`; sample at `docs/samples/bakeoff.yaml`; tutorial at
  `docs/tutorial-bakeoff.md`.
```

- [ ] **Step 4: Commit**

```bash
git add docs/quickstart-bakeoff.md README.md CHANGELOG.md
git commit -m "docs: bakeoff quickstart + README + CHANGELOG (SC-011)"
```

## ⛔ DC-FINAL Drift Check

Re-read mission brief. Evidence per SC:

- **SC-001:** `chunkshop bakeoff --help` runs; CLI tested via smoke step in Task 8.
- **SC-002:** `test_bakeoff_config.py` — minimal parses, empty matrix rejected, extra fields forbidden, scoring defaults.
- **SC-003:** `test_bakeoff_config.py` — gold queries from file (YAML + JSON) and inline.
- **SC-004:** `test_bakeoff_keys.py` + runner creates tables via `combo_table()`.
- **SC-005:** `test_bakeoff_scoring.py` — known rank cases + aggregation.
- **SC-006:** `test_bakeoff_output.py::test_results_json_round_trips`.
- **SC-007:** `test_bakeoff_output.py::test_report_md_has_leaderboard_and_stat_note`.
- **SC-008:** `test_bakeoff_output.py::test_recommended_yaml_parses_as_cell_config`.
- **SC-009:** `docs/samples/bakeoff.yaml` + `bakeoff-gold.yaml`.
- **SC-010:** `docs/tutorial-bakeoff.md`.
- **SC-011:** `docs/quickstart-bakeoff.md`.
- **SC-012:** Manual walkthrough against fresh Postgres.
- **SC-013:** `test_bakeoff_e2e.py`.

**Verify:**

```bash
cd python && uv run pytest -q       # all green, 105+ passing (was 93 + 12+ new)
```

## Notes for the executing agent

- **No worktree — work directly on main.** This feature is additive and the commit history is reviewed per commit. (Follow the commit-per-task scheme.)
- **Don't delete or alter `scripts/bench_matrix.py`.** It stays as the internal hacking tool. The CLI is a parallel user-facing surface.
- **Heavy fixtures:** the e2e test downloads 3 ONNX models (~200 MB). Cache is `~/.cache/fastembed/`. If you're dispatched to an agent sandbox that can't reach HuggingFace, the e2e test will fail — in that case run it locally, not in the sandbox.
- **Pydantic alias for `schema`:** `BakeoffTargetConfig.schema_name` uses `Field(alias="schema")` because `schema` is a pydantic reserved name. User YAML uses `schema`; Python code reads `schema_name`. Match how `TargetConfig` handles this already.
- **CLI import order:** top-of-file imports should not pull chunkshop.bakeoff eagerly — use in-function imports in the `bakeoff` click command to keep the subcommand optional-dep-friendly (future-proofing against bakeoff ever pulling heavy deps).
- **After implementation:** if `uv run pytest -q` fails with count != 105+, stop and diagnose. Don't commit partial.
