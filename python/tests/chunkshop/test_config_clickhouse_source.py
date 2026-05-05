"""Config-load tests for ClickhouseTableSource (P1-SC-001)."""
import pytest
from pydantic import ValidationError

from chunkshop.config import ClickhouseTableSource


def test_minimum_valid_config_parses():
    cfg = ClickhouseTableSource(
        type="clickhouse_table",
        dsn_env="CHUNKSHOP_TEST_DSN_CH",
        database="my_app",
        table="documents",
        id_column="id",
        content_column="body",
    )
    assert cfg.database_name == "my_app"   # alias=database
    assert cfg.title_column is None
    assert cfg.where is None
    assert cfg.metadata_columns == []


def test_full_config_parses():
    cfg = ClickhouseTableSource(
        type="clickhouse_table",
        dsn_env="CHUNKSHOP_TEST_DSN_CH",
        database="my_app",
        table="documents",
        id_column="id",
        content_column="body",
        title_column="headline",
        where="created_at > toDateTime('2025-01-01 00:00:00')",
        metadata_columns=["lang", "author"],
    )
    assert cfg.title_column == "headline"
    assert cfg.where.startswith("created_at >")
    assert cfg.metadata_columns == ["lang", "author"]


def test_typo_rejected_extra_forbid():
    with pytest.raises(ValidationError) as ei:
        ClickhouseTableSource(
            type="clickhouse_table",
            dsn_env="X", database="d", table="t",
            id_column="id", content_column="body",
            metadata_colmns=["x"],   # typo
        )
    assert "metadata_colmns" in str(ei.value)


def test_wrong_type_rejected():
    with pytest.raises(ValidationError):
        ClickhouseTableSource(
            type="not_a_real_type",
            dsn_env="X", database="d", table="t",
            id_column="id", content_column="body",
        )


def test_load_source_dispatches_clickhouse_table():
    from chunkshop.sources import load_source
    cfg = ClickhouseTableSource(
        type="clickhouse_table",
        dsn_env="CHUNKSHOP_TEST_DSN_CH",
        database="my_app", table="documents",
        id_column="id", content_column="body",
    )
    src = load_source(cfg)
    assert type(src).__name__ == "ClickhouseTableSource"
