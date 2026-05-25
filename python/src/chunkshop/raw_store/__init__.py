# src/chunkshop/raw_store/__init__.py
"""RawStore factory — dispatch on the config discriminator, mirroring load_sink."""
from chunkshop.raw_store.base import RawStore
from chunkshop.raw_store.local import LocalRawStore


def load_raw_store(cfg) -> RawStore:
    if cfg.type == "local":
        return LocalRawStore(root=cfg.root)
    if cfg.type == "s3":
        from chunkshop.raw_store.s3 import S3RawStore
        return S3RawStore(bucket=cfg.bucket, prefix=cfg.prefix, endpoint_url=cfg.endpoint_url)
    raise ValueError(f"unknown raw_store type: {cfg.type!r}")


__all__ = ["RawStore", "LocalRawStore", "load_raw_store"]
