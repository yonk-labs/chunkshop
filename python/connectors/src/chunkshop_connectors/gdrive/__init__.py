"""Verified Google Drive connector entry-point surface.

Registered via the ``chunkshop.sources`` entry point ``gdrive``.
Consumers configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: gdrive
      config:
        folder_id: 0BabcXYZ                  # OR `query`
        scopes:
          - https://www.googleapis.com/auth/drive.readonly
        oauth_tokens: ${GDRIVE_OAUTH_TOKENS} # optional — env fallback used
                                             #   if omitted

OAuth tokens are produced by
:class:`chunkshop_connectors.oauth.google.GoogleOAuthProvider`. The
config field accepts a serialised ``OAuthTokens`` dict (the same shape
the dataclass produces); the connector resolves it lazily on first
API call.

Sync mode is ``cursor`` — cursor shape is
``{"page_token": "<token>"}``. The connector seeds the token on first
sync via ``GET /drive/v3/changes/startPageToken``, and advances it
via ``GET /drive/v3/changes?pageToken=...`` on subsequent syncs.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chunkshop_connectors.gdrive.connector import GDriveConnector as Connector


# Drive folder IDs are URL-safe random strings; the connector is
# stricter than Google's "anything goes" so YAML typos blow up fast.
_FOLDER_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_id: Optional[str] = None
    query: Optional[str] = None
    scopes: list[str] = Field(
        default_factory=lambda: ["https://www.googleapis.com/auth/drive.readonly"]
    )
    # Serialised OAuthTokens dict (access_token, refresh_token,
    # expires_at, scopes, provider, provider_extras). When omitted the
    # connector reads ${GDRIVE_OAUTH_TOKENS} (JSON-encoded) at first
    # API call.
    oauth_tokens: Optional[dict] = None
    drive_base_url: str = "https://www.googleapis.com/drive/v3"

    @model_validator(mode="after")
    def _need_folder_or_query(self) -> "ConfigModel":
        if not self.folder_id and not self.query:
            raise ValueError(
                "gdrive config: either `folder_id` or `query` is required"
            )
        return self

    @model_validator(mode="after")
    def _safe_folder(self) -> "ConfigModel":
        if self.folder_id is not None and not _FOLDER_RE.match(self.folder_id):
            raise ValueError(
                f"folder_id must match {_FOLDER_RE.pattern!r}, got {self.folder_id!r}"
            )
        return self

    def __repr__(self) -> str:
        # Redact oauth_tokens in repr — they contain access/refresh
        # tokens that must never leak to logs / crash reporters.
        oauth_repr = "<redacted>" if self.oauth_tokens else None
        return (
            f"ConfigModel(folder_id={self.folder_id!r}, query={self.query!r}, "
            f"scopes={self.scopes!r}, oauth_tokens={oauth_repr}, "
            f"drive_base_url={self.drive_base_url!r})"
        )


def factory(config: dict[str, Any]) -> Connector:
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
