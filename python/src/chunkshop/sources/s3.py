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
from chunkshop.sources.base import Document, SyncMode


class S3Source:
    # Exposed as CURSOR: the cursor is the full {key: etag} map. Unchanged ETags
    # are skipped on re-sync (FINGERPRINT semantics, surfaced as a cursor so
    # consumers persist a single opaque dict between runs).
    sync_mode = SyncMode.CURSOR

    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def _client(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 source requires boto3. Install with `pip install chunkshop[s3]` "
                "or `uv sync --extra s3`."
            ) from exc
        return boto3.client("s3", endpoint_url=self.cfg.endpoint_url)

    def _list(self, client):
        # Paginate the listing — buckets can have >1000 keys.
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.cfg.bucket, Prefix=self.cfg.prefix):
            for obj in page.get("Contents") or []:
                yield obj["Key"], obj.get("ETag", ""), int(obj.get("Size", 0))

    def _fetch(self, client, key, etag, size) -> Document:
        body = client.get_object(Bucket=self.cfg.bucket, Key=key)["Body"].read()
        return Document(
            id=f"s3://{self.cfg.bucket}/{key}",
            content=body.decode("utf-8", errors="replace"),
            title=None,
            metadata={"bucket": self.cfg.bucket, "key": key, "size": size, "etag": etag},
            fingerprint=etag,
        )

    def iter_documents(self) -> Iterator[Document]:
        client = self._client()
        for key, etag, size in list(self._list(client)):
            yield self._fetch(client, key, etag, size)

    def empty_cursor(self) -> dict:
        return {}

    def iter_changes_since(self, cursor: dict) -> Iterator[Document]:
        client = self._client()
        for key, etag, size in list(self._list(client)):
            if cursor.get(key) != etag:
                yield self._fetch(client, key, etag, size)

    def cursor_from(self, last_document: Document) -> dict:
        # The canonical S3 cursor is the full key→etag map; the consumer
        # accumulates fingerprints across the batch and persists that map.
        # This per-doc helper supports batch checkpointing — the consumer
        # merges each returned single-key dict into the running cursor.
        meta = last_document.metadata or {}
        return {meta.get("key", last_document.id): last_document.fingerprint}
