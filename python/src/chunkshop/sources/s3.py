"""S3 source — list a bucket+prefix and fetch each object as a Document.

Uses ``boto3`` (optional ``[s3]`` extra). Imports lazily so chunkshop core
doesn't pull boto3 unless the user's YAML actually requests this source.

Document shape per fetched object:
    id        = ``s3://<bucket>/<key>``
    content   = response body decoded as utf-8
    title     = None
    metadata  = ``{"bucket": str, "key": str, "size": int, "etag": str}``

Pagination is handled via ``list_objects_v2``'s paginator. Auth + region
resolution: standard AWS credential chain (env → ~/.aws/credentials → IAM
role). The optional ``endpoint_url`` lets users point at minio / R2 / other
S3-compatible servers without code changes.

Sequential fetch — no retries, no rate limiting, no parallelism. Fail-fast on
the first error.
"""
from __future__ import annotations

from typing import Iterator

from chunkshop.config import S3Source as Cfg
from chunkshop.sources.base import Document


class S3Source:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def iter_documents(self) -> Iterator[Document]:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 source requires boto3. Install with `pip install chunkshop[s3]` "
                "or `uv sync --extra s3`."
            ) from exc

        client = boto3.client("s3", endpoint_url=self.cfg.endpoint_url)

        # Paginate the listing — buckets can have >1000 keys.
        paginator = client.get_paginator("list_objects_v2")
        keys: list[tuple[str, int, str]] = []
        for page in paginator.paginate(Bucket=self.cfg.bucket, Prefix=self.cfg.prefix):
            for obj in page.get("Contents") or []:
                keys.append((obj["Key"], int(obj.get("Size", 0)), obj.get("ETag", "")))

        out: list[Document] = []
        for key, size, etag in keys:
            resp = client.get_object(Bucket=self.cfg.bucket, Key=key)
            body = resp["Body"].read().decode("utf-8", errors="replace")
            out.append(Document(
                id=f"s3://{self.cfg.bucket}/{key}",
                content=body,
                title=None,
                metadata={
                    "bucket": self.cfg.bucket,
                    "key": key,
                    "size": size,
                    "etag": etag,
                },
            ))
        return iter(out)
