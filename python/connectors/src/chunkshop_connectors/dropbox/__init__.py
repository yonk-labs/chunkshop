"""Verified Dropbox connector entry-point surface.

Registered via the ``chunkshop.sources`` entry point ``dropbox``.
Consumers configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: dropbox
      config:
        folder_path: "/Apps/chunkshop"     # default "" = account root
        recursive: true                    # default true
        include_extensions:                # default = common text MIMEs
          - ".md"
          - ".txt"
        token: ${DROPBOX_TOKEN}            # optional — env fallback

Auth is bearer-token-based. Dropbox supports both short-lived OAuth
tokens and long-lived app access tokens; the connector treats them
identically and never tries to refresh.

Sync mode is ``cursor`` — cursor shape is ``{"cursor": "<opaque>"}``.
The connector uses Dropbox's own ``/files/list_folder/continue``
delta API so the cursor is the Dropbox cursor verbatim.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from chunkshop_connectors.dropbox.connector import DropboxConnector as Connector


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Optional[str] = None  # falls back to ${DROPBOX_TOKEN} at runtime
    folder_path: str = ""
    recursive: bool = True
    include_extensions: Optional[list[str]] = None
    base_url: str = "https://api.dropboxapi.com/2"
    content_url: str = "https://content.dropboxapi.com/2"

    def __repr__(self) -> str:
        # Redact the bearer token so it doesn't leak via logs.
        token_repr = "***" if self.token else None
        return (
            f"ConfigModel(folder_path={self.folder_path!r}, "
            f"recursive={self.recursive!r}, "
            f"include_extensions={self.include_extensions!r}, "
            f"token={token_repr}, base_url={self.base_url!r}, "
            f"content_url={self.content_url!r})"
        )


def factory(config: dict[str, Any]) -> Connector:
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
