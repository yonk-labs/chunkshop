"""Cross-backend pipeline: MariaDB source → PG sink.

Skipped unless BOTH $CHUNKSHOP_TEST_DSN (PG) and $CHUNKSHOP_TEST_DSN_MARIADB are set.
"""
import os
import pytest

pytest.importorskip("pymysql")

from chunkshop.backends.mariadb import MariaDBBackend
from chunkshop.backends.postgres import PostgresBackend
from chunkshop.config import (
    MariaDbTableSource, TargetConfig, FastembedEmbedder, NoneExtractor,
    SentenceAwareChunker, IdentityFramerConfig, RuntimeConfig, CellConfig,
)
from chunkshop.runner import run_cell


PG_DSN_VAR = "CHUNKSHOP_TEST_DSN"
MARIADB_DSN_VAR = "CHUNKSHOP_TEST_DSN_MARIADB"
PG_DSN = os.environ.get(PG_DSN_VAR)
MARIADB_DSN = os.environ.get(MARIADB_DSN_VAR)
pytestmark = pytest.mark.skipif(
    not (PG_DSN and MARIADB_DSN),
    reason=f"both {PG_DSN_VAR} and {MARIADB_DSN_VAR} required",
)


def test_sc007_read_mariadb_write_pg():
    """SC-007: a cell that reads MariaDB and writes PG completes end-to-end."""
    src_db = "chunkshop_xb_src"
    sink_db = "chunkshop_xb_sink"

    # Seed source data in MariaDB
    be_md = MariaDBBackend(dsn_env=MARIADB_DSN_VAR)
    with be_md.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS `{src_db}`")
        cur.execute(f"CREATE DATABASE `{src_db}`")
        cur.execute(f"""
            CREATE TABLE `{src_db}`.`docs` (
                id VARCHAR(64) PRIMARY KEY,
                body TEXT NOT NULL
            )
        """)
        cur.execute(
            f"INSERT INTO `{src_db}`.`docs` VALUES (%s, %s)",
            ("doc1", "Hello world. This is a test sentence. " * 10),
        )
        conn.commit()

    cfg = CellConfig(
        cell_name="xb_test",
        source=MariaDbTableSource(
            type="mariadb_table", dsn_env=MARIADB_DSN_VAR, database=src_db,
            table="docs", id_column="id", content_column="body",
        ),
        framer=IdentityFramerConfig(),
        chunker=SentenceAwareChunker(max_chars=200, min_chars=50),
        embedder=FastembedEmbedder(
            type="fastembed",
            model_name="Xenova/bge-base-en-v1.5-int8",
            dim=768, threads=2, batch_size=8,
        ),
        extractor=NoneExtractor(),
        target=TargetConfig(
            type="postgres", dsn_env=PG_DSN_VAR, database=sink_db,
            table="chunks", mode="overwrite", source_tag="xb_test", hnsw=False,
        ),
        runtime=RuntimeConfig(omp_num_threads=2),
    )

    result = run_cell(cfg)
    assert result.error is None
    assert result.docs_processed == 1
    assert result.chunks_written > 0

    # Verify PG side
    be_pg = PostgresBackend(dsn_env=PG_DSN_VAR)
    with be_pg.connect() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{sink_db}"."chunks"')
        assert cur.fetchone()[0] == result.chunks_written

    # Cleanup
    with be_md.connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE `{src_db}`")
        conn.commit()
    with be_pg.connect() as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA "{sink_db}" CASCADE')
        conn.commit()
