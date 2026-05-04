"""Pydantic config models for `chunkshop bakeoff` runs (SC-002, SC-003).

One `BakeoffConfig` = one matrix evaluation: a corpus, a set of gold queries,
and a cross-product of chunkers x embedders to rank with recall@k + MRR.

v4 multi-backend: `targets` is a discriminated-union list (postgres, mariadb,
sqlite). The same chunks + vectors are written into every target, then queried
through each backend's native vector syntax. The leaderboard is rendered
side-by-side so accuracy parity (or divergence) shows up at a glance and
ingest/query latency differences are directly comparable.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

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
    """One user-authored evaluation query. Doc-level gold only for MVP."""

    query: str
    gold_doc_id: str


class MatrixConfig(_Base):
    """Cross-product axes. N embedders x M chunkers = N*M combos."""

    embedders: list[FastembedEmbedder] = Field(..., min_length=1)
    chunkers: list[ChunkerConfig] = Field(..., min_length=1)


class _BakeoffTargetBase(_Base):
    """Common fields for every backend's bakeoff target.

    `database_name` (YAML alias `database`) parallels TargetConfig. SQLite
    ignores it at runtime (file path comes from `dsn_env`), but it's still
    required for loose parity.
    """

    dsn_env: str
    database_name: str = Field(alias="database")


class PostgresBakeoffTarget(_BakeoffTargetBase):
    type: Literal["postgres"]


class MariadbBakeoffTarget(_BakeoffTargetBase):
    type: Literal["mariadb"]


class SqliteBakeoffTarget(_BakeoffTargetBase):
    type: Literal["sqlite"]


BakeoffTarget = Annotated[
    Union[PostgresBakeoffTarget, MariadbBakeoffTarget, SqliteBakeoffTarget],
    Field(discriminator="type"),
]


class ScoringConfig(_Base):
    """Retrieval-metric config. `k` controls recall cutoffs; `top_k` is sink LIMIT."""

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
    """Top-level bakeoff run config. One YAML = one factorial run."""

    name: str
    source: SourceConfig
    framer: FramerConfig = Field(default_factory=IdentityFramerConfig)
    gold_queries: Union[str, list[GoldQuery]]  # path to YAML/JSON file OR inline list
    matrix: MatrixConfig
    targets: list[BakeoffTarget] = Field(..., min_length=1)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    output_dir: Optional[str] = None
    runtime: Optional[RuntimeConfig] = None


class ComboResult(_Base):
    """Scored outcome for one (backend, chunker, embedder) cell."""

    backend: str  # "postgres" / "mariadb" / "sqlite"
    chunker_key: str
    embedder_key: str
    chunker_label: str
    embedder_label: str
    table: str
    ingest_chunks: int
    ingest_wall_seconds: float
    # Subset of ingest_wall_seconds spent inside the embedder. Lets the
    # leaderboard distinguish "this combo is slow because of the embedder"
    # from "this combo is slow because of the chunker / sink". 0.0 if the
    # embedder didn't track timing.
    ingest_embed_seconds: float = 0.0
    # Total wall time spent issuing top_k queries against this cell during
    # the scoring phase. Per-backend latency comparison sits on this number.
    query_wall_seconds: float = 0.0
    aggregate: dict[str, float]
    per_query: list[dict[str, Any]]


class BakeoffResults(_Base):
    """Full output of `run_bakeoff`. Round-trips through JSON via pydantic."""

    run_name: str
    started_at: str
    corpus_label: str
    n_queries: int
    n_combos: int
    combos: list[ComboResult]
    gold_queries: list[dict[str, str]]
    # Wall time per unique embedder spent embedding all gold queries during
    # the scoring phase. Indicative of query-time latency at production
    # scale: the value scaled by your expected QPS predicts CPU cost.
    # Keys are embedder_key (same as ComboResult.embedder_key); values are
    # seconds for embedding all `n_queries` queries in one batch.
    query_embed_seconds_by_embedder: dict[str, float] = {}
