import pytest

pytest.importorskip("pymysql")

from chunkshop.backends.mariadb import MariaDBBackend


@pytest.fixture
def be():
    return MariaDBBackend(dsn_env="DUMMY_DSN")


def test_name_and_supports_upsert(be):
    assert be.name == "mariadb"
    assert be.supports_upsert is True


def test_quote_ident_uses_backticks(be):
    assert be.quote_ident("my_table") == "`my_table`"


def test_quote_ident_escapes_embedded_backtick(be):
    assert be.quote_ident("weird`name") == "`weird``name`"


def test_fq_table(be):
    assert be.fq_table("chunkshop", "chunks") == "`chunkshop`.`chunks`"
