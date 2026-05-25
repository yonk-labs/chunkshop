# src/chunkshop/raw_store/local.py
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Optional


class LocalRawStore:
    """Filesystem RawStore. Layout: <root>/<sha256(doc_id)>/{blob,meta.json}.

    doc_id is hashed so arbitrary ids (s3://…, paths with ../) cannot traverse
    outside root. The original doc_id is recorded in meta.json.
    """

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, doc_id: str) -> Path:
        h = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()
        return self.root / h

    def put(self, doc_id: str, data: bytes, *, content_type: str,
            meta: Optional[dict] = None) -> str:
        d = self._dir(doc_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "blob").write_bytes(data)
        record = {"doc_id": doc_id, "content_type": content_type, **(meta or {})}
        (d / "meta.json").write_text(json.dumps(record))
        return str(d / "blob")

    def get(self, ref: str) -> bytes:
        return Path(ref).read_bytes()

    def exists(self, doc_id: str, fingerprint: Optional[str] = None) -> bool:
        d = self._dir(doc_id)
        if not (d / "blob").exists():
            return False
        if fingerprint is None:
            return True
        try:
            meta = json.loads((d / "meta.json").read_text())
        except FileNotFoundError:
            return False
        return meta.get("fingerprint") == fingerprint

    def delete(self, doc_id: str) -> None:
        d = self._dir(doc_id)
        for f in ("blob", "meta.json"):
            (d / f).unlink(missing_ok=True)
        if d.exists():
            try:
                d.rmdir()
            except OSError:
                pass
