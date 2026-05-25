"""Verified GitLab connector entry-point surface.

Registered via the ``chunkshop.sources`` entry point ``gitlab``.
Consumers configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: gitlab
      config:
        project: acme/widgets             # "namespace/project" or "12345"
        branch: main                      # optional, default "main"
        paths_glob: ["**/*.md"]           # optional, default = all files
        token: ${GITLAB_TOKEN}            # optional — env-var fallback
        base_url: https://gitlab.com/api/v4  # override for self-hosted

Auth is PAT-only — Personal Access Token, group access token, or
project access token. The connector sends it via the
``PRIVATE-TOKEN`` header, which works for all three.

Sync mode is ``cursor`` — cursor shape is
``{"after_commit_sha": "<sha>"}`` (identical to the github connector).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chunkshop_connectors.gitlab.connector import GitLabConnector as Connector


# Project IDs are either a positive integer (as a string) or
# ``namespace/project[/subgroup]`` paths. Allow alphanumerics, dots,
# dashes, underscores, and slashes inside.
_PROJECT_RE = re.compile(r"^(?:\d+|[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+)$")
# GitLab branch names follow git's rules; same loose check the
# github connector uses.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = Field(..., min_length=1)
    branch: str = "main"
    paths_glob: Optional[list[str]] = None
    token: Optional[str] = None  # falls back to ${GITLAB_TOKEN} at runtime
    base_url: str = "https://gitlab.com/api/v4"

    @field_validator("project")
    @classmethod
    def _safe_project(cls, v: str) -> str:
        if not _PROJECT_RE.match(v):
            raise ValueError(
                f"project must be a numeric ID or 'namespace/project[...]', got {v!r}"
            )
        return v

    @field_validator("branch")
    @classmethod
    def _safe_branch(cls, v: str) -> str:
        if not _BRANCH_RE.match(v):
            raise ValueError(f"branch must match {_BRANCH_RE.pattern!r}, got {v!r}")
        return v

    def __repr__(self) -> str:
        token_repr = "***" if self.token else None
        return (
            f"ConfigModel(project={self.project!r}, branch={self.branch!r}, "
            f"paths_glob={self.paths_glob!r}, token={token_repr}, "
            f"base_url={self.base_url!r})"
        )


def factory(config: dict[str, Any]) -> Connector:
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
