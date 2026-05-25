# src/chunkshop/raw_store/s3.py
"""S3 RawStore backend (optional [s3] extra). Key layout: <prefix><sha256(doc_id)>.
Fingerprint is stored in object metadata for exists(doc_id, fingerprint) checks."""
from __future__ import annotations
import hashlib
from typing import Optional


class S3RawStore:
    def __init__(self, bucket: str, prefix: str = "", endpoint_url: Optional[str] = None):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "S3 raw_store requires boto3. Install with `pip install chunkshop[s3]`."
            ) from exc
        self.bucket = bucket
        self.prefix = prefix
        self._client = boto3.client("s3", endpoint_url=endpoint_url)

    def _key(self, doc_id: str) -> str:
        return self.prefix + hashlib.sha256(doc_id.encode("utf-8")).hexdigest()

    def put(self, doc_id, data, *, content_type, meta=None):
        md = {"doc_id": doc_id}
        if meta and "fingerprint" in meta:
            md["fingerprint"] = str(meta["fingerprint"])
        key = self._key(doc_id)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                ContentType=content_type, Metadata=md)
        return f"s3://{self.bucket}/{key}"

    def get(self, ref):
        _, _, rest = ref.partition("s3://")
        bucket, _, key = rest.partition("/")
        return self._client.get_object(Bucket=bucket, Key=key)["Body"].read()

    def exists(self, doc_id, fingerprint=None):
        from botocore.exceptions import ClientError
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=self._key(doc_id))
        except ClientError:
            return False
        if fingerprint is None:
            return True
        return resp.get("Metadata", {}).get("fingerprint") == fingerprint

    def delete(self, doc_id):
        self._client.delete_object(Bucket=self.bucket, Key=self._key(doc_id))
