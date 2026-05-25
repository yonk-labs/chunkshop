"""Verified blob-storage connector entry-point surface.

Registered via ``chunkshop.sources`` entry point ``blob``. Consumers
configure with a YAML ``ConnectorSource``::

    source:
      type: connector
      connector: blob
      config:
        bucket: my-bucket
        prefix: docs/
        endpoint_url: https://s3.us-east-1.amazonaws.com  # optional
        region: us-east-1                                 # optional
        access_key: ${AWS_ACCESS_KEY_ID}                  # optional
        secret_key: ${AWS_SECRET_ACCESS_KEY}              # optional

When ``access_key``/``secret_key`` are unset, boto3's standard
credential resolution applies (env vars, ``~/.aws/credentials``, IAM
role on EC2/ECS, etc).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from chunkshop_connectors.blob.connector import BlobConnector as Connector


class ConfigModel(BaseModel):
    """Pydantic schema for the blob connector's ``config`` dict.

    ``extra="forbid"`` mirrors chunkshop's house style: a typo in YAML
    becomes a load-time validation error, not a silent ignore.
    """
    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(..., min_length=1)
    prefix: str = ""
    endpoint_url: Optional[str] = None
    region: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None


def factory(config: dict[str, Any]) -> Connector:
    """Entry-point factory.

    Validates ``config`` against ``ConfigModel`` so misconfigured
    cells fail at load time with a clear error rather than at
    iteration time with a boto3 traceback.
    """
    validated = ConfigModel.model_validate(config)
    return Connector(validated.model_dump(exclude_none=False))


__all__ = ["Connector", "ConfigModel", "factory"]
