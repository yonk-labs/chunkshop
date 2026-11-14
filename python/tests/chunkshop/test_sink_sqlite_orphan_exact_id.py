"""delete_orphans must remove only the shrinking doc's own vector rows (no LIKE wildcards / case folding)."""
import numpy as np
import pytest

pytest.importorskip("sqlite_vec")

from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig
from chunkshop.sinks.sqlite import SqliteSink


def _chunks(doc_id, n):
    return [Chunk(doc_id=doc_id, seq_num=i, original_content=f"c{i}",
                  embedded_content=f"c{i}", metadata={}) for i in range(n)]


def _sink(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_ORPHAN_PATH", str(tmp_path / "o.db"))
    cfg = TargetConfig(type="sqlite", dsn_env="SQLITE_ORPHAN_PATH", database="ignored",
                       table="chunks", mode="overwrite", source_tag="t1", delete_orphans=True)
    sink = SqliteSink(cfg, SQLiteBackend(dsn_env="SQLITE_ORPHAN_PATH"), embed_dim=4)
    sink.create_table()
    return sink


def _write(sink, doc_id, n):
    emb = np.random.rand(n, 4).astype(np.float32)
    sink.write_document(doc_id, _chunks(doc_id, n), emb, [[]] * n)


def _ids(sink, fq):
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM {fq} ORDER BY id")
        return [r[0] for r in cur.fetchall()]


@pytest.mark.parametrize("big,small", [("Notes", "notes"), ("release-notes", "release_notes")])
def test_shrinking_one_doc_leaves_lookalike_doc_intact(tmp_path, monkeypatch, big, small):
    sink = _sink(tmp_path, monkeypatch)
    _write(sink, big, 4)
    _write(sink, small, 3)
    _write(sink, small, 1)  # shrink
    want = sorted([f"{big}::{i}" for i in range(4)] + [f"{small}::0"])
    assert _ids(sink, sink._fq_main()) == want
    assert _ids(sink, sink._fq_vec()) == want


def test_shrink_still_removes_own_orphans(tmp_path, monkeypatch):
    sink = _sink(tmp_path, monkeypatch)
    _write(sink, "Notes", 4)
    _write(sink, "Notes", 2)
    assert _ids(sink, sink._fq_main()) == ["Notes::0", "Notes::1"]
    assert _ids(sink, sink._fq_vec()) == ["Notes::0", "Notes::1"]
