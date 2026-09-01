from chunkshop.config import PromoteColumn


def test_content_origin_column_name_is_safe():
    pc = PromoteColumn(path="content_origin", type="text")
    assert pc.column_name == "content_origin"


def test_derived_rung_column_name_is_safe():
    pc = PromoteColumn(path="derived_rung", type="text")
    assert pc.column_name == "derived_rung"


def test_default_media_promotions_are_exported():
    from chunkshop.config import DEFAULT_MEDIA_PROMOTIONS

    names = {pc.column_name for pc in DEFAULT_MEDIA_PROMOTIONS}
    assert {"content_origin", "derived_rung"} <= names


def test_media_cell_gets_origin_columns_automatically():
    import yaml

    from chunkshop.config import CellConfig

    cfg = CellConfig(**yaml.safe_load("""
    cell_name: t
    source:
      type: files
      glob: /tmp/*.png
      derive: {png: {rung: image_meta}}
    chunker: {type: fixed_overlap, window_words: 128, step_words: 0}
    embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    extractor: {type: none}
    target: {type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: t, mode: overwrite, hnsw: false}
    """))
    names = {pc.column_name for pc in cfg.target.promote_metadata}
    assert {"content_origin", "derived_rung"} <= names


def test_user_promotion_is_not_duplicated():
    import yaml

    from chunkshop.config import CellConfig

    cfg = CellConfig(**yaml.safe_load("""
    cell_name: t
    source:
      type: files
      glob: /tmp/*.png
      derive: {png: {rung: image_meta}}
    chunker: {type: fixed_overlap, window_words: 128, step_words: 0}
    embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    extractor: {type: none}
    target:
      type: sqlite
      dsn_env: SQLITE_PATH
      database: ignored
      table: t
      mode: overwrite
      hnsw: false
      promote_metadata: [{path: content_origin, type: text}]
    """))
    names = [pc.column_name for pc in cfg.target.promote_metadata]
    assert names.count("content_origin") == 1


def test_text_only_cell_gets_no_media_columns():
    """NN-6: a cell with no derive block is untouched."""
    import yaml

    from chunkshop.config import CellConfig

    cfg = CellConfig(**yaml.safe_load("""
    cell_name: t
    source: {type: files, glob: /tmp/*.md}
    chunker: {type: fixed_overlap, window_words: 128, step_words: 0}
    embedder: {type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768}
    extractor: {type: none}
    target: {type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: t, mode: overwrite, hnsw: false}
    """))
    assert not cfg.target.promote_metadata
