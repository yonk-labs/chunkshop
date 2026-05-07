#!/usr/bin/env python3
"""Seed the cross-language parity fixture into MariaDB via the Python sink.

Used by the Rust integration test tests/mariadb_cross_lang_parity.rs to verify
that vectors written by Python's MariaDbSink are readable+queryable by the
Rust crate.

Usage:
    export CHUNKSHOP_TEST_DSN_MARIADB=mysql://root:rootpw@localhost:3307/chunkshop_test
    uv --project python run python python/scripts/seed_mariadb_cross_lang_fixture.py
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    REPO_ROOT
    / "rust"
    / "chunkshop"
    / "tests"
    / "parity-fixtures"
    / "mariadb-cross-lang.json"
)

sys.path.insert(0, str(REPO_ROOT / "python" / "src"))
from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.sinks.mariadb import MariaDbSink
from chunkshop.config import TargetConfig
from chunkshop.chunkers.base import Chunk


def main() -> int:
    if "CHUNKSHOP_TEST_DSN_MARIADB" not in os.environ:
        print("CHUNKSHOP_TEST_DSN_MARIADB not set — aborting", file=sys.stderr)
        return 2
    fixture = json.loads(FIXTURE.read_text())
    dim = fixture["embed_dim"]

    cfg = TargetConfig(
        type="mariadb",
        dsn_env="CHUNKSHOP_TEST_DSN_MARIADB",
        database="chunkshop_xlang",
        table="parity",
        mode="overwrite",
        source_tag="cross_lang_fixture",
        hnsw=False,
    )
    backend = MariaDBBackend(dsn_env=cfg.dsn_env)
    sink = MariaDbSink(cfg=cfg, backend=backend, embed_dim=dim)
    sink.create_table()

    for c in fixture["chunks"]:
        chunk = Chunk(
            doc_id=c["doc_id"],
            seq_num=c["seq_num"],
            original_content=c["original_content"],
            embedded_content=c["embedded_content"],
            metadata=c["metadata"],
        )
        emb = np.array([c["embedding"]], dtype=np.float32)
        tags = [c["tags"]]
        sink.write_document(chunk.doc_id, [chunk], emb, tags)

    print(f"Seeded {len(fixture['chunks'])} chunks into chunkshop_xlang.parity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
