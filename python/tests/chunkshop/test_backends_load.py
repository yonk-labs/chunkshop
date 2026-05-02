import pytest
from chunkshop.backends import load_backend


def test_load_backend_postgres():
    be = load_backend(name="postgres", dsn_env="DUMMY_DSN")
    assert be.name == "postgres"


def test_load_backend_unknown():
    with pytest.raises(ValueError, match="unknown backend"):
        load_backend(name="oracle", dsn_env="X")
