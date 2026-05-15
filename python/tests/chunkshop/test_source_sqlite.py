"""SC-004: SqliteTableSource reads source rows."""
import pytest

pytest.importorskip("sqlite_vec")

from chunkshop.backends.sqlite import SQLiteBackend
from chunkshop.config import SqliteTableSource
from chunkshop.sources.sqlite_table import SqliteTableSource as Source


def test_sc004_iter_documents(tmp_path, monkeypatch):
    db = tmp_path / "src.db"
    monkeypatch.setenv("SQLITE_SRC_PATH", str(db))
    be = SQLiteBackend(dsn_env="SQLITE_SRC_PATH")
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute('CREATE TABLE "docs" (id TEXT PRIMARY KEY, body TEXT, lang TEXT)')
        cur.executemany('INSERT INTO "docs" VALUES (?, ?, ?)', [
            ("a", "first body", "en"),
            ("b", "second body", "fr"),
        ])
        conn.commit()

    cfg = SqliteTableSource(
        type="sqlite_table", dsn_env="SQLITE_SRC_PATH", database="ignored",
        table="docs", id_column="id", content_column="body",
        metadata_columns=["lang"],
    )
    docs = list(Source(cfg).iter_documents())
    assert len(docs) == 2
    by_id = {d.id: d for d in docs}
    assert by_id["a"].content == "first body"
    assert by_id["a"].metadata == {"lang": "en"}
    assert by_id["b"].metadata == {"lang": "fr"}
