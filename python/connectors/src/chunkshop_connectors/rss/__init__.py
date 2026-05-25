"""Verified RSS connector entry-point surface.

Registered via ``chunkshop.sources`` entry point ``rss``. Consumers
configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: rss
      config:
        url: https://example.com/feed.xml
        timeout: 30          # optional
        user_agent: chunkshop/0.2   # optional
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from chunkshop_connectors.rss.connector import RssConnector as Connector


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1)
    timeout: int = 30
    user_agent: Optional[str] = None


def factory(config: dict[str, Any]) -> Connector:
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
