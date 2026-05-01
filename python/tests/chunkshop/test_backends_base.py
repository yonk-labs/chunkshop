import pytest
from dataclasses import FrozenInstanceError
from chunkshop.backends.base import ColSpec, Backend


def test_colspec_is_frozen():
    c = ColSpec(name="id", type_ddl="text", nullable=False, is_primary_key=True)
    assert c.name == "id"
    assert c.is_primary_key is True
    with pytest.raises(FrozenInstanceError):
        c.name = "different"


def test_colspec_defaults():
    c = ColSpec(name="metadata", type_ddl="jsonb")
    assert c.nullable is True
    assert c.default is None
    assert c.is_primary_key is False


def test_backend_protocol_lists_required_attrs():
    # Protocol membership is structural; just verify the Protocol class exposes
    # the documented surface either as a method (hasattr) or as a typed attribute
    # (in __annotations__). Hedge against accidental rename of either kind.
    typed_attrs = {"name", "supports_upsert"}
    methods = {
        "connect", "quote_ident", "fq_table",
        "vector_type_ddl", "json_type_ddl", "tags_array_type_ddl",
        "text_pk_type_ddl", "timestamp_now_default_ddl",
        "vector_literal", "tags_literal", "json_literal",
        "json_path_sql", "upsert_clause",
        "create_database_sql", "add_column_if_not_exists_sql",
        "drop_table_sql", "emit_chunks_table_ddl",
        "table_exists", "embedding_dim", "with_create_lock",
    }
    for attr in typed_attrs:
        assert attr in Backend.__annotations__, f"Backend missing typed attr: {attr}"
    for attr in methods:
        assert hasattr(Backend, attr), f"Backend missing method: {attr}"
