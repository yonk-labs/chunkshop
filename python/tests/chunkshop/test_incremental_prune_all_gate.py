"""Incremental files run that would prune EVERY known doc is refused unless allow_prune_all (PYS-17)."""
import numpy as np
import pytest

pytest.importorskip("sqlite_vec")

import chunkshop.runner as runner
from chunkshop.config import CellConfig
from chunkshop.runner import run_cell
from chunkshop.sinks import load_sink

NAME = "chunkshop_review_p1b_pa"
DIM = 8


class _FakeEmb:
    embed_seconds = 0.0

    def embed(self, texts):
        v = np.random.default_rng(0).random((len(texts), DIM)).astype("float32")
        return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(runner, "load_embedder", lambda cfg: _FakeEmb())


def _cfg(tmp_path, glob, allow=None):
    inc = {"cursor_path": str(tmp_path / "cursor.json")}
    if allow is not None:
        inc["allow_prune_all"] = allow
    return CellConfig(**{
        "cell_name": NAME,
        "source": {"type": "files", "glob": glob, "id_from": "stem", "incremental": inc},
        "chunker": {"type": "fixed_overlap", "window_words": 5, "step_words": 5},
        "embedder": {"type": "fastembed", "model_name": "x", "dim": DIM},
        "target": {"type": "sqlite", "dsn": str(tmp_path / "t.db"), "database": "main",
                   "table": NAME, "mode": "create_if_missing", "hnsw": False},
    })


def _doc_ids(cfg):
    sink = load_sink(cfg.target, embed_dim=DIM)
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT doc_id FROM {sink._fq_main()} ORDER BY doc_id")
        return sorted(r[0] for r in cur.fetchall())


def _seed(tmp_path, allow=None):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for n in "abc":
        (corpus / f"{n}.md").write_text(f"{n} {n} {n} words here\n")
    cfg = _cfg(tmp_path, str(corpus / "*.md"), allow)
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["a", "b", "c"]
    return corpus, cfg


def test_all_files_gone_is_refused_and_nothing_changes(tmp_path):
    corpus, cfg = _seed(tmp_path)
    cursor = (tmp_path / "cursor.json").read_text()
    for f in corpus.glob("*.md"):
        f.unlink()
    res = run_cell(cfg)
    assert res.error
    assert "3 document" in res.error and "allow_prune_all" in res.error and str(corpus) in res.error
    assert _doc_ids(cfg) == ["a", "b", "c"]
    assert (tmp_path / "cursor.json").read_text() == cursor


def test_allow_prune_all_true_prunes_everything(tmp_path):
    corpus, cfg = _seed(tmp_path, allow=True)
    for f in corpus.glob("*.md"):
        f.unlink()
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == []


def test_partial_deletion_prunes_without_flag(tmp_path):
    corpus, cfg = _seed(tmp_path)
    (corpus / "b.md").unlink()
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["a", "c"]
