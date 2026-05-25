"""Verified Notion connector entry-point surface.

Registered via the ``chunkshop.sources`` entry point ``notion``.
Consumers configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: notion
      config:
        database_id: 0a1b2c3d-...         # OR `page_ids`
        # page_ids: ["abc-...", "def-..."]
        token: ${NOTION_TOKEN}             # optional — env-var fallback
                                           # used if omitted
        notion_version: "2022-06-28"       # optional, pins API version

Auth is integration-token-only (Notion's "internal integration"
token). OAuth public-integration support is intentionally out of
scope for v1 — most production ingest runs with a workspace-scoped
internal integration anyway.

Sync mode is ``cursor`` — cursor shape is
``{"after_last_edited_time": "<ISO8601>"}``. The connector advances
it to the max ``last_edited_time`` observed across emitted pages.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from chunkshop_connectors.notion.connector import NotionConnector as Connector


# Notion IDs are 32-char UUIDs that the API accepts either with or
# without hyphens. Lock the validator to "hex + optional hyphens" so
# typos blow up before the network call.
_NOTION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Optional[str] = None  # falls back to ${NOTION_TOKEN} at runtime
    database_id: Optional[str] = None
    page_ids: Optional[list[str]] = None
    notion_version: str = "2022-06-28"
    base_url: str = "https://api.notion.com/v1"

    @model_validator(mode="after")
    def _need_database_or_pages(self) -> "ConfigModel":
        if not self.database_id and not self.page_ids:
            raise ValueError(
                "notion config: either `database_id` or `page_ids` is required"
            )
        if self.database_id and self.page_ids:
            raise ValueError(
                "notion config: pass `database_id` OR `page_ids`, not both"
            )
        return self

    @model_validator(mode="after")
    def _safe_ids(self) -> "ConfigModel":
        if self.database_id is not None and not _NOTION_ID_RE.match(self.database_id):
            raise ValueError(
                f"database_id must look like a Notion UUID, got {self.database_id!r}"
            )
        if self.page_ids:
            for pid in self.page_ids:
                if not _NOTION_ID_RE.match(pid):
                    raise ValueError(
                        f"page_ids entry must look like a Notion UUID, got {pid!r}"
                    )
        return self

    def __repr__(self) -> str:
        # Redact the integration token in __repr__ so it doesn't leak
        # via logs / crash reporters.
        token_repr = "***" if self.token else None
        return (
            f"ConfigModel(database_id={self.database_id!r}, "
            f"page_ids={self.page_ids!r}, "
            f"notion_version={self.notion_version!r}, "
            f"token={token_repr}, base_url={self.base_url!r})"
        )


def factory(config: dict[str, Any]) -> Connector:
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
