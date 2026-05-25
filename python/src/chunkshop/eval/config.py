"""Configuration models for the higher-level RAG evaluation harness.

This harness is intentionally separate from `chunkshop bakeoff`. Bakeoff is a
customer-facing comparison runner; this config describes internal e2e
evaluation policies that combine baselines, retrieval settings, context
packing, answer generation, and llm-judge scoring.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Flexible(BaseModel):
    """Base model that preserves unknown fields for policy-specific knobs."""

    model_config = ConfigDict(extra="allow")


class JudgeProvider(_Flexible):
    provider: str = "openai-compatible"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None


class JudgingConfig(_Flexible):
    cache_dir: str = ".llm-judge-cache"
    resume: bool = True
    smoke_mode: str = "quick"
    final_mode: str = "accurate"
    concurrency: int = 4
    parse_retries: int = 1
    retries: int = 2
    timeout: float = 180
    temperature: float = 0
    max_tokens: Optional[int] = 1200
    strict_json_fallback: bool = True
    judges: list[JudgeProvider] = Field(default_factory=list)


class WorkloadConfig(_Flexible):
    name: str
    kind: str = "custom_corpus"
    tags: list[str] = Field(default_factory=list)
    profile: Optional[str] = None
    input: Optional[str] = None
    questions: Optional[str] = None
    gold: Optional[str] = None


class NamedPolicy(_Flexible):
    name: str
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class AxesConfig(_Flexible):
    """Named axis groups such as embedders, chunkers, retrieval modes."""

    pass


class ProfileConfig(_Flexible):
    include: dict[str, list[str]] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    description: Optional[str] = None


class ReportingConfig(_Flexible):
    top_n: int = 20
    compare_against: list[str] = Field(default_factory=list)
    specialist_profiles: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


class EvalMatrixConfig(_Flexible):
    name: str
    tags: list[str] = Field(default_factory=list)
    judging: JudgingConfig = Field(default_factory=JudgingConfig)
    workloads: list[WorkloadConfig] = Field(default_factory=list)
    baselines: list[NamedPolicy] = Field(default_factory=list)
    axes: AxesConfig = Field(default_factory=AxesConfig)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    candidates: list[NamedPolicy] = Field(default_factory=list)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)

    @field_validator("workloads")
    @classmethod
    def _workload_names_unique(cls, v: list[WorkloadConfig]) -> list[WorkloadConfig]:
        _assert_unique([w.name for w in v], "workload")
        return v

    @field_validator("baselines")
    @classmethod
    def _baseline_names_unique(cls, v: list[NamedPolicy]) -> list[NamedPolicy]:
        _assert_unique([b.name for b in v], "baseline")
        return v

    @field_validator("candidates")
    @classmethod
    def _candidate_names_unique(cls, v: list[NamedPolicy]) -> list[NamedPolicy]:
        _assert_unique([c.name for c in v], "candidate")
        return v


def _assert_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    dupes = sorted({value for value in values if value in seen or seen.add(value)})
    if dupes:
        raise ValueError(f"duplicate {label} name(s): {', '.join(dupes)}")


def load_eval_matrix(path: Path) -> EvalMatrixConfig:
    """Load and validate an eval matrix YAML/JSON file."""

    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"eval matrix must be a mapping: {path}")
    return EvalMatrixConfig.model_validate(data)
