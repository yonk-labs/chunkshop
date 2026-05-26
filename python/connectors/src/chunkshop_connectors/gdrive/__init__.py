"""Verified Google Drive connector entry-point surface.

Registered via the ``chunkshop.sources`` entry point ``gdrive``.
Consumers configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: gdrive
      config:
        folder_id: 0BabcXYZ                  # OR `query` OR `file_ids`
        scopes:
          - https://www.googleapis.com/auth/drive.readonly
        oauth_tokens: ${GDRIVE_OAUTH_TOKENS} # optional — env fallback used
                                             #   if omitted

Two selection modes, mutually exclusive:

* **Folder/query** (``folder_id`` and/or ``query``) — walks the folder
  via ``files.list`` and syncs incrementally off the Drive ``/changes``
  feed (cursor ``{"page_token": "<token>"}``).
* **Explicit IDs** (``file_ids: [<id>, ...]``) — ingests exactly the
  given files (e.g. the rows a UI picker selected). No folder walk;
  each file is fetched directly via ``files.get``. Sync is a
  modified-time delta (cursor ``{file_id: modifiedTime}``): only files
  whose ``modifiedTime`` advanced are re-emitted. Set ``reprocess:
  true`` to force re-emit of every selected file regardless of
  ``modifiedTime``.

OAuth tokens are produced by
:class:`chunkshop_connectors.oauth.google.GoogleOAuthProvider`. The
config field accepts a serialised ``OAuthTokens`` dict (the same shape
the dataclass produces); the connector resolves it lazily on first
API call.
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
    # Explicit selection mode: ingest exactly these Drive file IDs (e.g.
    # the rows a UI picker selected). Mutually exclusive with
    # folder_id/query. Sync is modified-time delta, not the /changes feed.
    file_ids: Optional[list[str]] = None
    # file_ids mode only: re-emit every selected file on each sync
    # regardless of modifiedTime — forces the sink to overwrite even
    # unchanged documents.
    reprocess: bool = False
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
    def _need_selector(self) -> "ConfigModel":
        has_folder_query = bool(self.folder_id or self.query)
        has_file_ids = bool(self.file_ids)
        if not has_folder_query and not has_file_ids:
            raise ValueError(
                "gdrive config: one of `folder_id`, `query`, or `file_ids` "
                "is required"
            )
        if has_file_ids and has_folder_query:
            raise ValueError(
                "gdrive config: `file_ids` cannot be combined with "
                "`folder_id`/`query` — they are distinct selection modes"
            )
        return self

    @model_validator(mode="after")
    def _safe_folder(self) -> "ConfigModel":
        if self.folder_id is not None and not _FOLDER_RE.match(self.folder_id):
            raise ValueError(
                f"folder_id must match {_FOLDER_RE.pattern!r}, got {self.folder_id!r}"
            )
        return self

    @model_validator(mode="after")
    def _safe_file_ids(self) -> "ConfigModel":
        for fid in self.file_ids or []:
            if not _FOLDER_RE.match(fid):
                raise ValueError(
                    f"file_ids entries must match {_FOLDER_RE.pattern!r}, "
                    f"got {fid!r}"
                )
        return self

    def __repr__(self) -> str:
        # Redact oauth_tokens in repr — they contain access/refresh
        # tokens that must never leak to logs / crash reporters.
        oauth_repr = "<redacted>" if self.oauth_tokens else None
        return (
            f"ConfigModel(folder_id={self.folder_id!r}, query={self.query!r}, "
            f"file_ids={self.file_ids!r}, reprocess={self.reprocess!r}, "
            f"scopes={self.scopes!r}, oauth_tokens={oauth_repr}, "
            f"drive_base_url={self.drive_base_url!r})"
        )


def factory(config: dict[str, Any]) -> Connector:
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
