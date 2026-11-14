"""Incremental prune / shrink must also remove the frames of non-identity framers.

Frames are written under ``f"{raw.id}#{n}"``; the runner used to delete by the
raw id only, so deleted/shrunk documents left stale rows forever (PYD-03).
"""
import os

import numpy as np
import pytest

import chunkshop.runner as runner
from chunkshop.chunkers.base import Chunk
from chunkshop.config import CellConfig, TargetConfig
from chunkshop.runner import run_cell
from chunkshop.sinks import load_sink

NAME = "chunkshop_review_pr006"
DIM = 8
PG_DSN = os.environ.get("CHUNKSHOP_TEST_DSN")
MARIA_DSN = os.environ.get("CHUNKSHOP_TEST_DSN_MARIADB")


class _FakeEmb:
    embed_seconds = 0.0

    def embed(self, texts):
        v = np.random.default_rng(0).random((len(texts), DIM)).astype("float32")
        return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(runner, "load_embedder", lambda cfg: _FakeEmb())


def _target(backend, tmp_path, **extra):
    if backend == "sqlite":
        pytest.importorskip("sqlite_vec")
        t = {"type": "sqlite", "dsn": str(tmp_path / "t.db"), "database": "main"}
    elif backend == "postgres":
        if not PG_DSN:
            pytest.skip("CHUNKSHOP_TEST_DSN not set")
        t = {"type": "postgres", "dsn": PG_DSN, "database": NAME}
    else:
        if not MARIA_DSN:
            pytest.skip("CHUNKSHOP_TEST_DSN_MARIADB not set")
        pytest.importorskip("pymysql")
        t = {"type": "mariadb", "dsn": MARIA_DSN, "database": NAME}
    t.update(table=NAME, mode="create_if_missing", hnsw=False)
    t.update(extra)
    return t


def _drop(backend):
    if backend == "postgres" and PG_DSN:
        import psycopg
        with psycopg.connect(PG_DSN) as c, c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{NAME}" CASCADE')
            c.commit()
    elif backend == "mariadb" and MARIA_DSN:
        from chunkshop.backends.mariadb import MariaDBBackend
        with MariaDBBackend(dsn=MARIA_DSN).connect() as conn:
            cur = conn.cursor()
            cur.execute(f"DROP DATABASE IF EXISTS `{NAME}`")
            conn.commit()


@pytest.fixture(params=["sqlite", "postgres", "mariadb"])
def backend(request):
    _drop(request.param)
    yield request.param
    _drop(request.param)


def _cfg(backend, tmp_path, framer, *, id_from="stem", orphans=True, incremental=True):
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    source = {"type": "files", "glob": str(corpus / "*.md"), "id_from": id_from}
    if incremental:
        source["incremental"] = {"cursor_path": str(tmp_path / "cursor.json")}
    return CellConfig(**{
        "cell_name": NAME,
        "source": source,
        "framer": framer,
        "chunker": {"type": "fixed_overlap", "window_words": 5, "step_words": 5},
        "embedder": {"type": "fastembed", "model_name": "x", "dim": DIM},
        "target": _target(backend, tmp_path, delete_orphans=orphans),
    })


def _doc_ids(cfg):
    sink = load_sink(cfg.target, embed_dim=DIM)
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT doc_id FROM {sink._fq()} ORDER BY doc_id"
                    if hasattr(sink, "_fq") else
                    f"SELECT DISTINCT doc_id FROM {sink._fq_main()} ORDER BY doc_id")
        return sorted(r[0] for r in cur.fetchall())


def _write(tmp_path, name, text):
    (tmp_path / "corpus").mkdir(exist_ok=True)
    (tmp_path / "corpus" / name).write_text(text)


HB = {"type": "heading_boundary"}


def test_a_shrunk_doc_and_deleted_doc_lose_stale_frames(backend, tmp_path):
    _write(tmp_path, "fr-a.md", "# One\nalpha alpha\n# Two\nbeta beta\n# Three\ngamma gamma\n")
    _write(tmp_path, "fr-b.md", "# Only\ndelta delta\n")
    cfg = _cfg(backend, tmp_path, HB)
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["fr-a#0", "fr-a#1", "fr-a#2", "fr-b#0"]
    _write(tmp_path, "fr-a.md", "# One\nalpha alpha edited\n")
    (tmp_path / "corpus" / "fr-b.md").unlink()
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["fr-a#0"]


def test_b_headings_removed_switches_to_plain_id(backend, tmp_path):
    _write(tmp_path, "x.md", "# One\nalpha alpha\n# Two\nbeta beta\n")
    cfg = _cfg(backend, tmp_path, HB)
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["x#0", "x#1"]
    _write(tmp_path, "x.md", "just plain words with no headings at all here\n")
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["x"]


def test_c_prune_without_delete_orphans_removes_frames(backend, tmp_path):
    _write(tmp_path, "fr-a.md", "# One\nalpha alpha\n# Two\nbeta beta\n")
    _write(tmp_path, "fr-b.md", "# Only\ndelta delta\n")
    cfg = _cfg(backend, tmp_path, HB, orphans=False)
    assert run_cell(cfg).error is None
    (tmp_path / "corpus" / "fr-b.md").unlink()
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["fr-a#0", "fr-a#1"]


def test_d_similar_ids_are_never_deleted(backend, tmp_path):
    _write(tmp_path, "notes.md", "one two three four five six seven\n")
    _write(tmp_path, "notes#draft.md", "eight nine ten eleven twelve thirteen\n")
    cfg = _cfg(backend, tmp_path, {"type": "identity"})
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["notes", "notes#draft"]
    (tmp_path / "corpus" / "notes.md").unlink()
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["notes#draft"]


def test_d2_live_doc_that_looks_like_a_frame_survives_prune(backend, tmp_path):
    """Identity framer: `meeting#1.md` is a real doc, not frame 1 of `meeting`."""
    _write(tmp_path, "meeting.md", "one two three four five six seven\n")
    _write(tmp_path, "meeting#1.md", "eight nine ten eleven twelve thirteen\n")
    cfg = _cfg(backend, tmp_path, {"type": "identity"})
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["meeting", "meeting#1"]
    (tmp_path / "corpus" / "meeting.md").unlink()
    assert run_cell(cfg).error is None
    assert _doc_ids(cfg) == ["meeting#1"]


def test_d_framed_doc_ids_exact_match_only(backend, tmp_path):
    cfg = _cfg(backend, tmp_path, {"type": "identity"})
    sink = load_sink(cfg.target, embed_dim=DIM)
    sink.create_table()
    ids = ["notes#0", "notes#12", "notes#draft", "Notes#1", "notes_x#1", "notes", "notes#"]
    for d in ids:
        ch = [Chunk(doc_id=d, seq_num=0, original_content="w", embedded_content="w", metadata={})]
        sink.write_document(d, ch, np.ones((1, DIM), dtype="float32"), [[]])
    assert sorted(sink.framed_doc_ids("notes")) == ["notes#0", "notes#12"]
    assert sink.framed_doc_ids("absent") == []


def test_e_non_incremental_run_never_looks_up_frames(backend, tmp_path, monkeypatch):
    _write(tmp_path, "fr-a.md", "# One\nalpha alpha\n# Two\nbeta beta\n")
    cfg = _cfg(backend, tmp_path, HB, incremental=False)
    calls = []
    cls = type(load_sink(cfg.target, embed_dim=DIM))
    orig = cls.framed_doc_ids
    monkeypatch.setattr(cls, "framed_doc_ids", lambda self, raw_id: calls.append(raw_id) or orig(self, raw_id))
    assert run_cell(cfg).error is None
    assert run_cell(cfg).error is None
    assert calls == []
    assert _doc_ids(cfg) == ["fr-a#0", "fr-a#1"]


def test_pipeline_delete_document_removes_frames(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    import chunkshop.pipeline as pipeline
    monkeypatch.setattr(pipeline, "load_embedder", lambda cfg: _FakeEmb())
    cfg = CellConfig(**{
        "cell_name": NAME,
        "source": {"type": "inline"},
        "framer": HB,
        "chunker": {"type": "fixed_overlap", "window_words": 5, "step_words": 5},
        "embedder": {"type": "fastembed", "model_name": "x", "dim": DIM},
        "target": _target("sqlite", tmp_path),
    })
    p = pipeline.Pipeline(cfg)
    p.ingest_text("d", "# One\nalpha alpha\n# Two\nbeta beta\n")
    p.ingest_text("d#draft", "other words entirely here\n")
    assert _doc_ids(cfg) == ["d#0", "d#1", "d#draft"]
    assert p.delete_document("d") == 2
    assert _doc_ids(cfg) == ["d#draft"]
