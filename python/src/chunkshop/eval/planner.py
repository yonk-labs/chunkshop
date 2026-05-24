"""Plan expansion for the RAG evaluation harness."""
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from chunkshop.eval.config import EvalMatrixConfig, NamedPolicy, WorkloadConfig


class EvalPolicy(BaseModel):
    """One concrete baseline/candidate/easy-mode policy."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str  # baseline, candidate, profile
    source: str
    tags: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class EvalRun(BaseModel):
    """One workload × policy execution cell."""

    model_config = ConfigDict(extra="forbid")

    id: str
    workload: str
    workload_kind: str
    policy: str
    policy_kind: str
    tags: list[str] = Field(default_factory=list)


class EvalPlan(BaseModel):
    """Expanded execution plan for a matrix config."""

    model_config = ConfigDict(extra="forbid")

    name: str
    config_hash: str
    workloads: list[dict[str, Any]]
    policies: list[EvalPolicy]
    runs: list[EvalRun]
    llm_judge_configs: list[str] = Field(default_factory=list)


def build_eval_plan(
    cfg: EvalMatrixConfig,
    *,
    profiles: Optional[Iterable[str]] = None,
    tags: Optional[Iterable[str]] = None,
    include_baselines: bool = True,
    include_candidates: bool = True,
) -> EvalPlan:
    """Expand a high-level matrix into concrete workload × policy cells.

    `profiles` selects named easy modes such as `general_default` or
    `accuracy_max`. If omitted, all configured profiles are expanded. Explicit
    `candidates` are included unless disabled.
    """

    selected_profiles = list(profiles) if profiles else list(cfg.profiles)
    selected_tags = set(tags or [])
    policies: list[EvalPolicy] = []

    if include_baselines:
        policies.extend(_baseline_policies(cfg.baselines))
    if include_candidates:
        policies.extend(_candidate_policies(cfg.candidates))
    policies.extend(_profile_policies(cfg, selected_profiles))

    if selected_tags:
        policies = [p for p in policies if selected_tags.intersection(p.tags)]
        workloads = [
            w for w in cfg.workloads
            if selected_tags.intersection(w.tags) or not w.tags
        ]
    else:
        workloads = list(cfg.workloads)

    runs: list[EvalRun] = []
    for workload in workloads:
        for policy in policies:
            run_id = f"{_slug(workload.name)}__{_slug(policy.name)}"
            run_tags = sorted(set(workload.tags) | set(policy.tags))
            runs.append(EvalRun(
                id=run_id,
                workload=workload.name,
                workload_kind=workload.kind,
                policy=policy.name,
                policy_kind=policy.kind,
                tags=run_tags,
            ))

    return EvalPlan(
        name=cfg.name,
        config_hash=_config_hash(cfg),
        workloads=[_workload_dump(w) for w in workloads],
        policies=policies,
        runs=runs,
    )


def write_eval_plan(
    cfg: EvalMatrixConfig,
    plan: EvalPlan,
    out_dir: Path,
    *,
    smoke_limit: int = 12,
) -> EvalPlan:
    """Write plan artifacts and llm-judge configs to an output directory."""

    out_dir.mkdir(parents=True, exist_ok=True)
    judge_dir = out_dir / "llm-judge"
    judge_dir.mkdir(parents=True, exist_ok=True)

    judge_config_paths = []
    for workload in cfg.workloads:
        if not workload.input or not workload.profile:
            continue
        for mode_name, mode in (
            ("smoke", cfg.judging.smoke_mode),
            ("final", cfg.judging.final_mode),
        ):
            path = judge_dir / f"{_slug(workload.name)}-{mode_name}.yaml"
            path.write_text(
                yaml.safe_dump(
                    _llm_judge_config(cfg, workload, mode=mode, limit=smoke_limit if mode_name == "smoke" else None),
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            judge_config_paths.append(str(path))

    plan = plan.model_copy(update={"llm_judge_configs": judge_config_paths})
    (out_dir / "manifest.json").write_text(
        plan.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_plan_report(plan), encoding="utf-8")
    return plan


def _baseline_policies(baselines: list[NamedPolicy]) -> list[EvalPolicy]:
    return [
        EvalPolicy(
            name=b.name,
            kind="baseline",
            source="baselines",
            tags=sorted(set(["baseline", *b.tags])),
            settings=_policy_settings(b),
        )
        for b in baselines
    ]


def _candidate_policies(candidates: list[NamedPolicy]) -> list[EvalPolicy]:
    return [
        EvalPolicy(
            name=c.name,
            kind="candidate",
            source="candidates",
            tags=sorted(set(["candidate", *c.tags])),
            settings=_policy_settings(c),
        )
        for c in candidates
    ]


def _profile_policies(cfg: EvalMatrixConfig, profile_names: list[str]) -> list[EvalPolicy]:
    policies: list[EvalPolicy] = []
    for profile_name in profile_names:
        if profile_name not in cfg.profiles:
            raise ValueError(f"unknown eval profile: {profile_name}")
        profile = cfg.profiles[profile_name]
        axis_values: list[tuple[str, list[dict[str, Any]]]] = []
        for axis_name, selected_names in profile.include.items():
            options = _axis_options(cfg, axis_name)
            by_name = {option["name"]: option for option in options}
            missing = [name for name in selected_names if name not in by_name]
            if missing:
                raise ValueError(
                    f"profile {profile_name!r} references missing {axis_name}: {', '.join(missing)}"
                )
            axis_values.append((axis_name, [by_name[name] for name in selected_names]))

        if not axis_values:
            policies.append(EvalPolicy(
                name=profile_name,
                kind="profile",
                source=f"profiles.{profile_name}",
                tags=sorted(set(["profile", profile_name, *profile.tags])),
                settings={},
            ))
            continue

        axis_names = [name for name, _values in axis_values]
        for combo in itertools.product(*(values for _name, values in axis_values)):
            settings = {axis_name: dict(option) for axis_name, option in zip(axis_names, combo)}
            suffix = "__".join(option["name"] for option in combo)
            policies.append(EvalPolicy(
                name=f"{profile_name}__{suffix}",
                kind="profile",
                source=f"profiles.{profile_name}",
                tags=sorted(set(["profile", profile_name, *profile.tags, *itertools.chain.from_iterable(o.get("tags", []) for o in combo)])),
                settings=settings,
            ))
    return policies


def _axis_options(cfg: EvalMatrixConfig, axis_name: str) -> list[dict[str, Any]]:
    raw = getattr(cfg.axes, axis_name, None)
    if raw is None and cfg.axes.model_extra:
        raw = cfg.axes.model_extra.get(axis_name)
    if raw is None:
        raise ValueError(f"unknown axis: {axis_name}")
    if not isinstance(raw, list):
        raise ValueError(f"axis {axis_name!r} must be a list")
    options = [_named_option(item) for item in raw]
    names = [option["name"] for option in options]
    if len(names) != len(set(names)):
        raise ValueError(f"axis {axis_name!r} has duplicate option names")
    return options


def _named_option(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"name": item, "value": item}
    if not isinstance(item, dict):
        raise ValueError(f"axis options must be strings or mappings, got {type(item).__name__}")
    if "name" not in item:
        raise ValueError(f"axis option is missing name: {item}")
    return dict(item)


def _policy_settings(policy: NamedPolicy) -> dict[str, Any]:
    data = policy.model_dump(exclude={"name", "description", "tags"}, exclude_none=True)
    if policy.description:
        data["description"] = policy.description
    return data


def _workload_dump(workload: WorkloadConfig) -> dict[str, Any]:
    return workload.model_dump(exclude_none=True)


def _llm_judge_config(
    cfg: EvalMatrixConfig,
    workload: WorkloadConfig,
    *,
    mode: str,
    limit: Optional[int],
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "input": workload.input,
        "profile": workload.profile,
        "out": f".llm-judge-runs/{cfg.name}-{workload.name}-{mode}",
        "mode": mode,
        "cache_dir": cfg.judging.cache_dir,
        "resume": cfg.judging.resume,
        "concurrency": cfg.judging.concurrency,
        "retries": cfg.judging.retries,
        "parse_retries": cfg.judging.parse_retries,
        "timeout": cfg.judging.timeout,
        "temperature": cfg.judging.temperature,
        "strict_json_fallback": cfg.judging.strict_json_fallback,
    }
    if cfg.judging.max_tokens is not None:
        data["max_tokens"] = cfg.judging.max_tokens
    if limit is not None:
        data["limit"] = limit
    if mode in {"accurate", "dual"}:
        data["judges"] = [
            provider.model_dump(exclude_none=True)
            for provider in cfg.judging.judges
        ]
    return data


def _plan_report(plan: EvalPlan) -> str:
    by_kind: dict[str, int] = {}
    for policy in plan.policies:
        by_kind[policy.kind] = by_kind.get(policy.kind, 0) + 1
    lines = [
        f"# Eval plan: {plan.name}",
        "",
        f"- Config hash: `{plan.config_hash}`",
        f"- Workloads: {len(plan.workloads)}",
        f"- Policies: {len(plan.policies)}",
        f"- Runs: {len(plan.runs)}",
        f"- Policy kinds: {', '.join(f'{k}={v}' for k, v in sorted(by_kind.items()))}",
        "",
        "## Workloads",
        "",
        "| Workload | Kind | Tags |",
        "|---|---|---|",
    ]
    for workload in plan.workloads:
        lines.append(
            f"| `{workload['name']}` | `{workload.get('kind', '')}` | "
            f"{', '.join(workload.get('tags', [])) or '-'} |"
        )
    lines += [
        "",
        "## Policies",
        "",
        "| Policy | Kind | Source | Tags |",
        "|---|---|---|---|",
    ]
    for policy in plan.policies:
        lines.append(
            f"| `{policy.name}` | `{policy.kind}` | `{policy.source}` | "
            f"{', '.join(policy.tags) or '-'} |"
        )
    if plan.llm_judge_configs:
        lines += ["", "## LLM Judge Configs", ""]
        for path in plan.llm_judge_configs:
            lines.append(f"- `{path}`")
    return "\n".join(lines) + "\n"


def _config_hash(cfg: EvalMatrixConfig) -> str:
    payload = cfg.model_dump_json(exclude_none=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")
