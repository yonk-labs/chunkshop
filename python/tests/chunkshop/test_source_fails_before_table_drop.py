"""An immediate source failure must not drop/empty an existing overwrite-mode table."""
import os

import numpy as np
import pytest

import chunkshop.runner as runner
from chunkshop.config import CellConfig
from chunkshop.runner import run_cell
from chunkshop.sinks import load_sink

NAME = "chunkshop_review_p1b_sf"
DIM = 8
PG_DSN = os.environ.get("CHUNKSHOP_TEST_DSN")


class _FakeEmb:
    embed_seconds = 0.0

    def embed(self, texts):
        v = np.random.default_rng(0).random((len(texts), DIM)).astype("float32")
        return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(runner, "load_embedder", lambda cfg: _FakeEmb())


def _drop():
    if PG_DSN:
        import psycopg
        with psycopg.connect(PG_DSN) as c, c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{NAME}" CASCADE')
            c.commit()


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request):
    if request.param == "postgres":
        if not PG_DSN:
            pytest.skip("CHUNKSHOP_TEST_DSN not set")
    else:
        pytest.importorskip("sqlite_vec")
    _drop()
    yield request.param
    _drop()


def _cfg(backend, tmp_path, glob, mode="overwrite", incremental=False):
    if backend == "sqlite":
        t = {"type": "sqlite", "dsn": str(tmp_path / "t.db"), "database": "main"}
    else:
        t = {"type": "postgres", "dsn": PG_DSN, "database": NAME}
    t.update(table=NAME, mode=mode, hnsw=False)
    source = {"type": "files", "glob": glob, "id_from": "stem"}
    if incremental:
        source["incremental"] = {"cursor_path": str(tmp_path / "cursor.json")}
    return CellConfig(**{
        "cell_name": NAME,
        "source": source,
        "chunker": {"type": "fixed_overlap", "window_words": 5, "step_words": 5},
        "embedder": {"type": "fastembed", "model_name": "x", "dim": DIM},
        "target": t,
    })


def _doc_ids(cfg):
    sink = load_sink(cfg.target, embed_dim=DIM)
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        fq = sink._fq() if hasattr(sink, "_fq") else sink._fq_main()
        cur.execute(f"SELECT DISTINCT doc_id FROM {fq} ORDER BY doc_id")
        return sorted(r[0] for r in cur.fetchall())


def test_unmatched_glob_leaves_overwrite_table_untouched(backend, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("alpha alpha alpha\n")
    (corpus / "b.md").write_text("beta beta beta\n")
    assert run_cell(_cfg(backend, tmp_path, str(corpus / "*.md"))).error is None
    bad = _cfg(backend, tmp_path, str(corpus / "*.nomatch"))
    res = run_cell(bad)
    assert res.error and "no files matched glob" in res.error
    assert _doc_ids(bad) == ["a", "b"]


def test_normal_overwrite_run_still_replaces_table(backend, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("alpha alpha alpha\n")
    (corpus / "b.md").write_text("beta beta beta\n")
    cfg = _cfg(backend, tmp_path, str(corpus / "*.md"))
    assert run_cell(cfg).error is None
    (corpus / "b.md").unlink()
    res = run_cell(cfg)
    assert res.error is None and res.docs_processed == 1
    assert _doc_ids(cfg) == ["a"]


def test_incremental_no_change_run_still_succeeds(backend, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("alpha alpha alpha\n")
    cfg = _cfg(backend, tmp_path, str(corpus / "*.md"), mode="create_if_missing", incremental=True)
    assert run_cell(cfg).error is None
    res = run_cell(cfg)
    assert res.error is None and res.docs_processed == 0
    assert _doc_ids(cfg) == ["a"]
