import os
import pytest

pytest.importorskip("pymysql")

from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.config import MariaDbTableSource
from chunkshop.sources.mariadb_table import MariaDbTableSource as Source

DSN_VAR = "CHUNKSHOP_TEST_DSN_MARIADB"
DSN = os.environ.get(DSN_VAR)
pytestmark = pytest.mark.skipif(not DSN, reason=f"{DSN_VAR} not set")


def test_sc006_iter_documents(monkeypatch):
    """SC-006: a MariaDB source can read source rows into the pipeline."""
    db_name = "chunkshop_src_test"
    be = MariaDBBackend(dsn_env=DSN_VAR)
    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
        cur.execute(f"DROP TABLE IF EXISTS `{db_name}`.`docs`")
        cur.execute(f"""
            CREATE TABLE `{db_name}`.`docs` (
                id VARCHAR(64) PRIMARY KEY,
                body TEXT NOT NULL,
                lang VARCHAR(8)
            )
        """)
        cur.execute(
            f"INSERT INTO `{db_name}`.`docs` VALUES (%s, %s, %s), (%s, %s, %s)",
            ("a", "first body", "en", "b", "second body", "fr"),
        )
        conn.commit()

    cfg = MariaDbTableSource(
        type="mariadb_table",
        dsn_env=DSN_VAR,
        database=db_name,
        table="docs",
        id_column="id",
        content_column="body",
        metadata_columns=["lang"],
    )
    src = Source(cfg)
    docs = list(src.iter_documents())
    assert len(docs) == 2
    by_id = {d.id: d for d in docs}
    assert by_id["a"].content == "first body"
    assert by_id["a"].metadata == {"lang": "en"}
    assert by_id["b"].metadata == {"lang": "fr"}

    with be.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE `{db_name}`")
        conn.commit()
