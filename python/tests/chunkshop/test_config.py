import textwrap
import pytest
from chunkshop.config import CellConfig, load_config


def test_loads_minimal_yaml(tmp_path):
    yaml = tmp_path / "c.yaml"
    yaml.write_text(textwrap.dedent("""
        cell_name: test_a_bge_small
        source:
          type: json_corpus
          path: /data/scotus.json
        chunker:
          type: sentence_aware
        embedder:
          type: fastembed
          model_name: BAAI/bge-small-en-v1.5
          dim: 384
        target:
          type: postgres
          dsn_env: AGE_BAKEOFF_PGRG_DSN
          database: factorial
          table: test_a_bge_small
        """))
    cfg = load_config(yaml)
    assert cfg.cell_name == "test_a_bge_small"
    assert cfg.source.type == "json_corpus"
    assert cfg.embedder.dim == 384
    assert cfg.target.table == "test_a_bge_small"
    assert cfg.extractor.type == "none"  # default
    assert cfg.runtime.omp_num_threads == 1  # default
    assert cfg.runtime.doc_limit is None  # default: all docs


def test_rejects_unknown_source_type(tmp_path):
    yaml = tmp_path / "c.yaml"
    yaml.write_text(textwrap.dedent("""
        cell_name: bad
        source:
          type: ftp
          url: ftp://bad
        chunker:
          type: sentence_aware
        embedder:
          type: fastembed
          model_name: x
          dim: 1
        target:
          type: postgres
          dsn_env: X
          database: factorial
          table: bad
        """))
    with pytest.raises(ValueError, match="ftp"):
        load_config(yaml)


def test_table_name_validated(tmp_path):
    yaml = tmp_path / "c.yaml"
    yaml.write_text(textwrap.dedent("""
        cell_name: bad_table
        source: {type: json_corpus, path: /x}
        chunker: {type: sentence_aware}
        embedder: {type: fastembed, model_name: x, dim: 1}
        target:
          type: postgres
          dsn_env: X
          database: factorial
          table: "weird name!"
        """))
    with pytest.raises(ValueError, match="table"):
        load_config(yaml)


def test_target_vector_metric_defaults_to_cosine(tmp_path):
    yaml = tmp_path / "c.yaml"
    yaml.write_text(textwrap.dedent("""
        cell_name: metric_default
        source: {type: json_corpus, path: /x}
        chunker: {type: sentence_aware}
        embedder: {type: fastembed, model_name: x, dim: 1}
        target:
          type: postgres
          dsn_env: X
          database: factorial
          table: ok
        """))
    cfg = load_config(yaml)
    assert cfg.target.vector_metric == "cosine"


def test_target_accepts_pgvector_metric(tmp_path):
    yaml = tmp_path / "c.yaml"
    yaml.write_text(textwrap.dedent("""
        cell_name: metric_ip
        source: {type: json_corpus, path: /x}
        chunker: {type: sentence_aware}
        embedder: {type: fastembed, model_name: x, dim: 1}
        target:
          type: postgres
          dsn_env: X
          database: factorial
          table: ok
          vector_metric: inner_product
        """))
    cfg = load_config(yaml)
    assert cfg.target.vector_metric == "inner_product"


def test_target_rejects_unknown_vector_metric(tmp_path):
    yaml = tmp_path / "c.yaml"
    yaml.write_text(textwrap.dedent("""
        cell_name: bad_metric
        source: {type: json_corpus, path: /x}
        chunker: {type: sentence_aware}
        embedder: {type: fastembed, model_name: x, dim: 1}
        target:
          type: postgres
          dsn_env: X
          database: factorial
          table: ok
          vector_metric: manhattan
        """))
    with pytest.raises(ValueError, match="vector_metric"):
        load_config(yaml)


def test_target_accepts_document_store_config(tmp_path):
    yaml = tmp_path / "c.yaml"
    yaml.write_text(textwrap.dedent("""
        cell_name: docs_enabled
        source: {type: json_corpus, path: /x}
        chunker: {type: sentence_aware}
        embedder: {type: fastembed, model_name: x, dim: 1}
        target:
          type: postgres
          dsn_env: X
          database: factorial
          table: chunks
          documents:
            enabled: true
            table: documents
            store_full_content: false
            store_lede_report: true
            promote_metadata:
              - path: lede_report.attributes.term.value
                type: text
            fts:
              enabled: true
              language: english
        """))
    cfg = load_config(yaml)
    assert cfg.target.documents.enabled is True
    assert cfg.target.documents.table == "documents"
    assert cfg.target.documents.store_full_content is False
    assert cfg.target.documents.promote_metadata[0].column_name == (
        "lede_report__attributes__term__value"
    )
    assert cfg.target.documents.fts.enabled is True


def test_target_documents_table_must_not_match_chunk_table(tmp_path):
    yaml = tmp_path / "c.yaml"
    yaml.write_text(textwrap.dedent("""
        cell_name: docs_bad_table
        source: {type: json_corpus, path: /x}
        chunker: {type: sentence_aware}
        embedder: {type: fastembed, model_name: x, dim: 1}
        target:
          type: postgres
          dsn_env: X
          database: factorial
          table: chunks
          documents:
            enabled: true
            table: chunks
        """))
    with pytest.raises(ValueError, match="documents.table"):
        load_config(yaml)
