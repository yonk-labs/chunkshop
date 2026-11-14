"""A non-incremental run with delete_orphans must remove rows of a doc that now yields zero chunks (PYD-04)."""
import numpy as np
import pytest

pytest.importorskip("sqlite_vec")

import chunkshop.runner as runner
from chunkshop.config import CellConfig
from chunkshop.runner import run_cell
from chunkshop.sinks import load_sink

NAME = "chunkshop_review_p1b_zc"
DIM = 8


class _FakeEmb:
    embed_seconds = 0.0

    def embed(self, texts):
        v = np.random.default_rng(0).random((len(texts), DIM)).astype("float32")
        return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(runner, "load_embedder", lambda cfg: _FakeEmb())


def _cfg(tmp_path, framer, orphans):
    (tmp_path / "corpus").mkdir(exist_ok=True)
    return CellConfig(**{
        "cell_name": NAME,
        "source": {"type": "files", "glob": str(tmp_path / "corpus" / "*.md"), "id_from": "stem"},
        "framer": framer,
        "chunker": {"type": "fixed_overlap", "window_words": 5, "step_words": 5},
        "embedder": {"type": "fastembed", "model_name": "x", "dim": DIM},
        "target": {"type": "sqlite", "dsn": str(tmp_path / "t.db"), "database": "main",
                   "table": NAME, "mode": "create_if_missing", "hnsw": False,
                   "delete_orphans": orphans},
    })


def _doc_ids(cfg):
    sink = load_sink(cfg.target, embed_dim=DIM)
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT doc_id FROM {sink._fq_main()} ORDER BY doc_id")
        return sorted(r[0] for r in cur.fetchall())


def test_zero_chunk_framed_doc_rows_removed_with_delete_orphans(tmp_path):
    # heading_boundary framer on a headingless doc keeps the plain id `a`;
    # emptying the file makes it yield zero chunks under that same id.
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "a.md").write_text("plain words with no headings at all here\n")
    (tmp_path / "corpus" / "b.md").write_text("# Only\ndelta delta\n")
    cfg = _cfg(tmp_path, {"type": "heading_boundary"}, orphans=True)
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["a", "b#0"]
    (tmp_path / "corpus" / "a.md").write_text("   \n")
    assert run_cell(cfg).error is None
    assert not [d for d in _doc_ids(cfg) if d.startswith("a")], _doc_ids(cfg)
    assert "b#0" in _doc_ids(cfg)


def test_zero_chunk_framed_doc_rows_kept_without_delete_orphans(tmp_path):
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "a.md").write_text("plain words with no headings at all here\n")
    cfg = _cfg(tmp_path, {"type": "heading_boundary"}, orphans=False)
    assert run_cell(cfg).error is None
    (tmp_path / "corpus" / "a.md").write_text("   \n")
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["a"]  # documents current semantics
