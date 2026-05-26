"""Verified GitHub connector entry-point surface.

Registered via the ``chunkshop.sources`` entry point ``github``.
Consumers configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: github
      config:
        owner: acme
        repo: widgets
        branch: main                # optional — omit to auto-detect the
                                    # repo's default_branch (#27)
        branch_strict: false        # optional — if true, a missing pinned
                                    # branch is a hard error (no fallback)
        clone: false                # optional — shallow-clone + walk the
                                    # tree locally instead of one API call
                                    # per file (#28); needs the git binary
        max_clone_mb: 200           # optional — refuse clones over this
        paths_glob: ["**/*.md"]     # optional, default = all files
        token: ${GITHUB_TOKEN}      # optional — env-var fallback used
                                    # if omitted

Auth is PAT-only (Personal Access Token). The required scope is
``repo`` for private repositories and ``public_repo`` for public.
See ``docs/connectors/github.md`` for the scope matrix.

Sync mode is ``cursor`` — cursor shape is
``{"after_commit_sha": "<sha>"}`` and the connector advances it by
querying the ``/compare`` REST endpoint between syncs.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chunkshop_connectors.github.connector import GitHubConnector as Connector


_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?$")
# Repo names are more permissive: letters, digits, hyphens, underscores, dots.
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Refs (branches): GitHub disallows things like spaces and "..", but for
# the connector's purposes a simple "no slashes, no controls" check is
# enough — leave deep validation to the GitHub server.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str = Field(..., min_length=1)
    repo: str = Field(..., min_length=1)
    # None → auto-detect the repo's default branch at runtime (#27).
    branch: Optional[str] = None
    # If a pinned branch 404s, fall back to the repo default unless strict.
    branch_strict: bool = False
    # When true, full syncs shallow-clone the repo and walk the working
    # tree locally instead of one /contents API call per file (#28).
    # Requires the `git` binary; falls back to the REST walk if absent.
    clone: bool = False
    # Refuse to process a shallow clone larger than this (MB) — bounds
    # disk footprint on accidental huge-repo attaches.
    max_clone_mb: int = 200
    paths_glob: Optional[list[str]] = None
    token: Optional[str] = None  # falls back to ${GITHUB_TOKEN} at runtime
    base_url: str = "https://api.github.com"

    @field_validator("owner")
    @classmethod
    def _safe_owner(cls, v: str) -> str:
        if not _OWNER_RE.match(v):
            raise ValueError(f"owner must match {_OWNER_RE.pattern!r}, got {v!r}")
        return v

    @field_validator("repo")
    @classmethod
    def _safe_repo(cls, v: str) -> str:
        if not _REPO_RE.match(v):
            raise ValueError(f"repo must match {_REPO_RE.pattern!r}, got {v!r}")
        return v

    @field_validator("branch")
    @classmethod
    def _safe_branch(cls, v: Optional[str]) -> Optional[str]:
        if v is None:  # auto-detect sentinel — nothing to validate
            return v
        if not _BRANCH_RE.match(v):
            raise ValueError(f"branch must match {_BRANCH_RE.pattern!r}, got {v!r}")
        return v

    def __repr__(self) -> str:
        # Redact the token in __repr__ so it doesn't leak via logs / traces.
        token_repr = "***" if self.token else None
        return (
            f"ConfigModel(owner={self.owner!r}, repo={self.repo!r}, "
            f"branch={self.branch!r}, paths_glob={self.paths_glob!r}, "
            f"token={token_repr}, base_url={self.base_url!r})"
        )


def factory(config: dict[str, Any]) -> Connector:
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
