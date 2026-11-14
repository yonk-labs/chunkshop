"""promote_metadata must accept the same values on every backend (PYD-07).

Postgres converts list/dict/scalar values per declared type; SQLite and MariaDB
used to hand the raw Python value to the driver and fail the whole cell.
"""
import json
import os

import numpy as np
import pytest

from chunkshop.chunkers.base import Chunk
from chunkshop.config import TargetConfig
from chunkshop.sinks import load_sink

NAME = "chunkshop_review_p1c"
PG_DSN = os.environ.get("CHUNKSHOP_TEST_DSN")
MARIA_DSN = os.environ.get("CHUNKSHOP_TEST_DSN_MARIADB")
DIM = 4


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


def _sink(backend, tmp_path, ptype):
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
    t.update(table=NAME, mode="create_if_missing", hnsw=False,
             promote_metadata=[{"path": "v", "type": ptype}])
    sink = load_sink(TargetConfig(**t), embed_dim=DIM)
    sink.create_table()
    return sink


def _roundtrip(sink, value):
    chunk = Chunk(doc_id="d1", seq_num=0, original_content="x",
                  embedded_content="x", metadata={"v": value})
    emb = np.random.default_rng(0).random((1, DIM)).astype("float32")
    sink.write_document("d1", [chunk], emb, [[]])
    fq = sink._fq() if hasattr(sink, "_fq") else sink._fq_main()
    with sink.backend.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT v FROM {fq}")
        return cur.fetchone()[0]


def _decoded(backend, raw):
    # psycopg already decodes jsonb; sqlite/mariadb return JSON text.
    return raw if backend == "postgres" else json.loads(raw)


def test_null(backend, tmp_path):
    assert _roundtrip(_sink(backend, tmp_path, "text"), None) is None


def test_string_text(backend, tmp_path):
    assert _roundtrip(_sink(backend, tmp_path, "text"), "abc") == "abc"


def test_int(backend, tmp_path):
    assert _roundtrip(_sink(backend, tmp_path, "int"), 7) == 7


def test_list_text_array(backend, tmp_path):
    raw = _roundtrip(_sink(backend, tmp_path, "text[]"), ["a", "b"])
    if backend == "postgres":
        assert raw == ["a", "b"]
    else:
        assert json.loads(raw) == ["a", "b"]


def test_dict_jsonb(backend, tmp_path):
    value = {"k": ["v", 1], "u": "é"}
    raw = _roundtrip(_sink(backend, tmp_path, "jsonb"), value)
    assert _decoded(backend, raw) == value


def test_list_jsonb(backend, tmp_path):
    raw = _roundtrip(_sink(backend, tmp_path, "jsonb"), ["a", 2])
    assert _decoded(backend, raw) == ["a", 2]


def test_dict_and_list_into_text(backend, tmp_path):
    raw = _roundtrip(_sink(backend, tmp_path, "text"), {"k": "é"})
    assert json.loads(raw) == {"k": "é"}


def test_scalar_into_text_array(backend, tmp_path):
    raw = _roundtrip(_sink(backend, tmp_path, "text[]"), "en")
    if backend == "postgres":
        assert raw == ["en"]
    elif backend == "mariadb":
        assert json.loads(raw) == "en"
    else:
        assert raw == "en"


def test_scalar_into_jsonb(backend, tmp_path):
    raw = _roundtrip(_sink(backend, tmp_path, "jsonb"), "en")
    if backend == "sqlite":
        assert raw == "en"
    else:
        assert _decoded(backend, raw) == "en"
