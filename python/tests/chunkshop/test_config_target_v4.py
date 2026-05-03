import pytest
from chunkshop.config import TargetConfig


def test_target_type_postgres_with_database_alias():
    cfg = TargetConfig(
        type="postgres",
        dsn_env="PG_DSN",
        database="chunkshop",
        table="my_chunks",
        mode="overwrite",
    )
    assert cfg.type == "postgres"
    assert cfg.database_name == "chunkshop"
    assert cfg.table == "my_chunks"


def test_target_rejects_unknown_type():
    with pytest.raises(Exception):
        TargetConfig(type="oracle", dsn_env="X", database="x", table="y", mode="overwrite")


def test_target_rejects_legacy_schema_field():
    with pytest.raises(Exception):
        TargetConfig(type="postgres", dsn_env="X", schema="x", table="y", mode="overwrite")


def test_target_rejects_legacy_overwrite_field():
    with pytest.raises(Exception):
        TargetConfig(type="postgres", dsn_env="X", database="x", table="y", overwrite=True)


def test_target_database_passes_ident_validator():
    with pytest.raises(Exception):
        TargetConfig(type="postgres", dsn_env="X", database="My-DB", table="y", mode="overwrite")
