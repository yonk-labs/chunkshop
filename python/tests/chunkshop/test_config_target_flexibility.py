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
