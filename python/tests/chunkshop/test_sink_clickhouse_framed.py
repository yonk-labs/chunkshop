"""ClickHouseSink.framed_doc_ids. Skipped unless $CHUNKSHOP_TEST_DSN_CH is set (not run in review)."""
import os
import numpy as np
import pytest

pytest.importorskip("clickhouse_connect")

from chunkshop.config import TargetConfig
from chunkshop.chunkers.base import Chunk
from chunkshop.sinks.clickhouse import ClickHouseSink
from chunkshop.backends.clickhouse import ClickHouseBackend

DSN_VAR = "CHUNKSHOP_TEST_DSN_CH"
DB = "chunkshop_review_pr006"
pytestmark = pytest.mark.skipif(not os.environ.get(DSN_VAR), reason=f"{DSN_VAR} not set")


@pytest.fixture(autouse=True)
def cleanup():
    be = ClickHouseBackend(dsn_env=DSN_VAR)
    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{DB}` SYNC")
    yield
    with be.connect() as client:
        client.command(f"DROP DATABASE IF EXISTS `{DB}` SYNC")


@pytest.mark.parametrize("tag", [None, "t1"])
def test_framed_doc_ids_exact_match_only(tag):
    cfg = TargetConfig(type="clickhouse", dsn_env=DSN_VAR, database=DB, table="chunks",
                       mode="overwrite", source_tag=tag)
    sink = ClickHouseSink(cfg, ClickHouseBackend(dsn_env=DSN_VAR), embed_dim=4)
    sink.create_table()
    for d in ["notes#0", "notes#12", "notes#draft", "Notes#1", "notes_x#1", "notes", "notes#"]:
        ch = [Chunk(doc_id=d, seq_num=0, original_content="w", embedded_content="w", metadata={})]
        sink.write_document(d, ch, np.ones((1, 4), dtype=np.float32), [[]])
    assert sink.framed_doc_ids("notes") == ["notes#0", "notes#12"]
    assert sink.framed_doc_ids("absent") == []
