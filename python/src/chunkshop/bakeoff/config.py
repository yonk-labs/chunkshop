"""Pydantic config models for `chunkshop bakeoff` runs (SC-002, SC-003).

One `BakeoffConfig` = one matrix evaluation: a corpus, a set of gold queries,
and a cross-product of chunkers x embedders to rank with recall@k + MRR.
Mirrors the existing `chunkshop.config.CellConfig` conventions (pydantic v2,
`extra="forbid"`, discriminated unions) so typos in YAML fail at config-load,
not at runtime.
"""
from __future__ import annotations

from typing import Any, Optional, Union

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


class BakeoffTargetConfig(_Base):
    """Where bakeoff tables land. One table per combo under `schema`."""

    dsn_env: str
    # `schema` is a pydantic reserved name; expose as `schema_name` in Python,
    # accept `schema` in YAML via alias. Matches `TargetConfig`'s treatment.
    schema_name: str = Field(alias="schema")


class ScoringConfig(_Base):
    """Retrieval-metric config. `k` controls recall cutoffs; `top_k` is pgvector LIMIT."""

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
    target: BakeoffTargetConfig
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    output_dir: Optional[str] = None
    runtime: Optional[RuntimeConfig] = None


class ComboResult(_Base):
    """Scored outcome for one (chunker, embedder) combo."""

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
