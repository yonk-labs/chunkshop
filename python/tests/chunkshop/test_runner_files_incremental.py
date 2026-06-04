# python/tests/chunkshop/test_runner_files_incremental.py
from pathlib import Path

from chunkshop.config import load_config
from chunkshop.runner import run_cell
from chunkshop.incremental_cursor import load_cursor


def _write_cell(tmp_path, corpus_dir, cursor_path, *, incremental=True,
                glob_suffix="*.md", chunker="sentence_aware"):
    """Build a files→sqlite cell YAML. Explicit line list (NOT dedent) so the
    conditional incremental block nests correctly."""
    lines = [
        "cell_name: files_inc",
        "source:",
        "  type: files",
        f"  glob: {corpus_dir}/**/{glob_suffix}",
        "  id_from: path",
    ]
    if incremental:
        lines += [
            "  incremental:",
            f"    cursor_path: {cursor_path}",
            "    detect: hash",
        ]
    lines += [
        "chunker:",
        f"  type: {chunker}",
        "embedder:",
        "  type: fastembed",
        "  model_name: BAAI/bge-small-en-v1.5",
        "  dim: 384",
        "target:",
        "  type: sqlite",
        f"  dsn: {tmp_path / 'vecs.db'}",
        "  database: main",
        "  table: files_inc",
        "  mode: create_if_missing",
        "  source_tag: files_inc",
        "  hnsw: false",
        "runtime:",
        "  omp_num_threads: 1",
    ]
    yaml_path = tmp_path / "cell.yaml"
    yaml_path.write_text("\n".join(lines) + "\n")
    return load_config(yaml_path)


def test_unchanged_rerun_processes_zero_docs(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("The first document about cats.")
    (corpus / "b.md").write_text("The second document about dogs.")
    cursor = tmp_path / "cur.json"
    cfg = _write_cell(tmp_path, corpus, cursor)

    r1 = run_cell(cfg)
    assert r1.error is None
    assert r1.docs_processed == 2 and r1.chunks_written >= 2
    assert load_cursor(cursor).keys() == {str(corpus / "a.md"), str(corpus / "b.md")}

    r2 = run_cell(cfg)  # nothing changed
    assert r2.error is None
    assert r2.docs_processed == 0 and r2.chunks_written == 0

    (corpus / "a.md").write_text("The first document, now about elephants.")
    r3 = run_cell(cfg)  # only a.md changed
    assert r3.error is None
    assert r3.docs_processed == 1


def test_deleted_file_is_pruned(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("alpha document about cats")
    (corpus / "b.md").write_text("beta document about dogs")
    cursor = tmp_path / "cur.json"
    cfg = _write_cell(tmp_path, corpus, cursor)
    run_cell(cfg)

    from chunkshop.sinks import load_sink
    sink = load_sink(cfg.target, embed_dim=cfg.embedder.dim)
    assert sink.count_docs() == 2

    (corpus / "b.md").unlink()
    r = run_cell(cfg)
    assert r.error is None
    sink2 = load_sink(cfg.target, embed_dim=cfg.embedder.dim)
    assert sink2.count_docs() == 1  # b.md's chunks pruned
    assert str(corpus / "b.md") not in load_cursor(cursor)


def test_no_incremental_block_is_full_resync_and_writes_no_cursor(tmp_path):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("alpha document about cats")
    cfg = _write_cell(tmp_path, corpus, tmp_path / "cur.json", incremental=False)
    r1 = run_cell(cfg)
    r2 = run_cell(cfg)
    assert r1.docs_processed == 1 and r2.docs_processed == 1  # full resync each run
    assert not (tmp_path / "cur.json").exists()  # no sidecar created


def test_crash_mid_run_leaves_prior_cursor_intact(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "a.md").write_text("alpha about cats")
    (corpus / "b.md").write_text("beta about dogs")
    cursor = tmp_path / "cur.json"
    cfg = _write_cell(tmp_path, corpus, cursor)
    run_cell(cfg)
    saved = load_cursor(cursor)

    (corpus / "a.md").write_text("alpha about cats v2")
    (corpus / "b.md").write_text("beta about dogs v2")

    import chunkshop.sinks.sqlite as sq
    calls = {"n": 0}
    real = sq.SqliteSink.write_document
    def boom(self, doc_id, chunks, embeddings, tags):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated mid-run crash")
        return real(self, doc_id, chunks, embeddings, tags)
    monkeypatch.setattr(sq.SqliteSink, "write_document", boom)

    r = run_cell(cfg)
    assert r.error is not None                  # runner caught the crash
    assert load_cursor(cursor) == saved         # cursor NOT advanced

    monkeypatch.undo()
    r2 = run_cell(cfg)                           # clean retry
    assert r2.error is None
    from chunkshop.sinks import load_sink
    assert load_sink(cfg.target, embed_dim=cfg.embedder.dim).count_docs() == 2  # no dup docs


def test_code_corpus_skips_unchanged_py_files(tmp_path):
    # Proves the cursor is content-agnostic: a .py corpus with a code chunker
    # behaves exactly like the prose corpus. code_aware uses the stdlib AST
    # (no [code] extra needed); symbol_aware behaves identically.
    corpus = tmp_path / "src"; corpus.mkdir()
    (corpus / "mod_a.py").write_text("def alpha():\n    return 1\n")
    (corpus / "mod_b.py").write_text("def beta():\n    return 2\n")
    cursor = tmp_path / "cur.json"
    cfg = _write_cell(tmp_path, corpus, cursor,
                      glob_suffix="*.py", chunker="code_aware")

    r1 = run_cell(cfg)
    assert r1.error is None and r1.docs_processed == 2
    r2 = run_cell(cfg)
    assert r2.error is None and r2.docs_processed == 0  # unchanged → skipped

    (corpus / "mod_a.py").write_text("def alpha():\n    return 42\n")
    r3 = run_cell(cfg)
    assert r3.error is None and r3.docs_processed == 1
