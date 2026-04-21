import pytest
from pydantic import ValidationError

from chunkshop.config import PromoteColumn, TargetConfig


def test_promote_column_valid():
    pc = PromoteColumn(path="language", type="text")
    assert pc.path == "language"
    assert pc.type == "text"


def test_promote_column_dotted_path():
    pc = PromoteColumn(path="entities.ORG", type="text[]")
    assert pc.path == "entities.ORG"


def test_promote_column_rejects_bad_ident():
    with pytest.raises(ValidationError):
        PromoteColumn(path="DROP TABLE", type="text")


def test_promote_column_rejects_bad_type():
    with pytest.raises(ValidationError):
        PromoteColumn(path="language", type="blob;DROP TABLE users")


def test_target_default_mode_is_overwrite():
    cfg = TargetConfig(dsn_env="X", **{"schema": "s"}, table="t")
    assert cfg.mode == "overwrite"
    assert cfg.source_tag is None
    assert cfg.promote_metadata == []
    assert cfg.force_overwrite is False


def test_target_append_requires_source_tag():
    with pytest.raises(ValidationError, match="source_tag"):
        TargetConfig(dsn_env="X", **{"schema": "s"}, table="t", mode="append")


def test_target_append_with_source_tag_ok():
    cfg = TargetConfig(
        dsn_env="X", **{"schema": "s"}, table="t",
        mode="append", source_tag="pdfs_q2_2026",
    )
    assert cfg.mode == "append"
    assert cfg.source_tag == "pdfs_q2_2026"


def test_target_source_tag_ident_safe():
    with pytest.raises(ValidationError):
        TargetConfig(
            dsn_env="X", **{"schema": "s"}, table="t",
            mode="append", source_tag="bad; drop table",
        )


def test_target_promote_metadata_parses():
    cfg = TargetConfig(
        dsn_env="X", **{"schema": "s"}, table="t",
        promote_metadata=[
            {"path": "language", "type": "text"},
            {"path": "entities.ORG", "type": "text[]"},
        ],
    )
    assert len(cfg.promote_metadata) == 2
    assert cfg.promote_metadata[0].path == "language"
    assert cfg.promote_metadata[1].type == "text[]"
