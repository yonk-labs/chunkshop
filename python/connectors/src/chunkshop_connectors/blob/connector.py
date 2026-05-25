"""Verified blob-storage connector (S3-compatible).

This is a clean-room reimplementation rather than a wholesale lift of
RAGFlow's ``common/data_source/blob_connector.py``. The upstream module
pulls in a deep stack of helpers (``utils.create_s3_client``,
``detect_bucket_region``, multi-cloud credential machinery, an internal
``Document`` model with sections/blob/source/extension fields) that
doesn't map cleanly onto chunkshop's text-first ``Document`` dataclass
without lifting half of ``_base/utils.py`` along with it. The behaviour
chunkshop's verified tier actually needs is narrower:

    1. Configure with ``{bucket, prefix, endpoint_url, access_key,
       secret_key, region}`` — credentials are baseline access-key auth
       only (Google-OAuth is out of scope for this tier; multi-cloud
       endpoints are supported via ``endpoint_url``).
    2. List bucket objects under ``prefix`` via ``list_objects_v2``.
    3. Fetch each object's body via ``get_object``.
    4. Yield chunkshop ``Document``s with the object's ETag as the
       fingerprint so chunkshop's fingerprint sync mode can detect
       per-object changes.

This narrows the surface area and keeps the connector importable
without dragging in moto / botocore stub generators.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator, Optional

from chunkshop.sources.base import Document, SyncMode

from chunkshop_connectors._tier import verified

logger = logging.getLogger(__name__)


@verified
class BlobConnector:
    """Verified-tier S3-compatible blob connector.

    Sync mode is ``FINGERPRINT``: each emitted Document carries the
    object's ETag as its fingerprint, and chunkshop's fingerprint sync
    path will only re-ingest objects whose ETag changed.
    """

    sync_mode = SyncMode.FINGERPRINT

    def __init__(self, config: dict[str, Any]) -> None:
        # Validation has already happened against ConfigModel before
        # the factory called us; defensively pull the keys we need.
        self.bucket: str = config["bucket"]
        self.prefix: str = config.get("prefix", "") or ""
        self.endpoint_url: Optional[str] = config.get("endpoint_url")
        self.region: Optional[str] = config.get("region")
        self.access_key: Optional[str] = config.get("access_key")
        self.secret_key: Optional[str] = config.get("secret_key")

        # Defer boto3 import so users who don't have the [blob] extra
        # installed can still import the package.
        import boto3  # noqa: PLC0415 -- intentional lazy import

        client_kwargs: dict[str, Any] = {}
        if self.endpoint_url:
            client_kwargs["endpoint_url"] = self.endpoint_url
        if self.region:
            client_kwargs["region_name"] = self.region
        if self.access_key and self.secret_key:
            client_kwargs["aws_access_key_id"] = self.access_key
            client_kwargs["aws_secret_access_key"] = self.secret_key

        self._client = boto3.client("s3", **client_kwargs)

    def iter_documents(self) -> Iterator[Document]:
        paginator = self._client.get_paginator("list_objects_v2")
        kwargs: dict[str, Any] = {"Bucket": self.bucket}
        if self.prefix:
            kwargs["Prefix"] = self.prefix
        for page in paginator.paginate(**kwargs):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if key.endswith("/"):
                    # skip pseudo-directory markers
                    continue
                etag = obj.get("ETag")
                try:
                    body_resp = self._client.get_object(Bucket=self.bucket, Key=key)
                except Exception:  # noqa: BLE001 -- per-object failure isolation
                    logger.exception("blob: failed to fetch %s/%s", self.bucket, key)
                    continue
                body = body_resp["Body"].read()
                if isinstance(body, bytes):
                    try:
                        content = body.decode("utf-8")
                    except UnicodeDecodeError:
                        content = body.decode("utf-8", errors="replace")
                else:
                    content = str(body)
                yield Document(
                    id=f"s3://{self.bucket}/{key}",
                    content=content,
                    title=key.rsplit("/", 1)[-1],
                    metadata={"bucket": self.bucket, "key": key},
                    fingerprint=etag,
                )
