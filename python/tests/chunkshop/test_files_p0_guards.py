"""P0 guards: files incremental vs overwrite (PYD-01), duplicate stem ids (PYS-01)."""
import pytest
from pydantic import ValidationError

from chunkshop.config import CellConfig, FilesSource as Cfg
from chunkshop.sources.files import FilesSource as Adapter


def _cell(tmp_path, *, mode=None, incremental=True):
    src = {"type": "files", "glob": str(tmp_path / "*.md")}
    if incremental:
        src["incremental"] = {"cursor_path": str(tmp_path / "cur.json")}
    target = {"type": "sqlite", "dsn": str(tmp_path / "v.db"), "database": "main", "table": "t"}
    if mode:
        target["mode"] = mode
    return {
        "cell_name": "c", "source": src,
        "chunker": {"type": "sentence_aware"},
        "embedder": {"type": "fastembed", "model_name": "BAAI/bge-small-en-v1.5", "dim": 384},
        "target": target,
    }


@pytest.mark.parametrize("mode", [None, "overwrite"])
def test_incremental_with_overwrite_rejected(tmp_path, mode):
    with pytest.raises(ValidationError) as ei:
        CellConfig(**_cell(tmp_path, mode=mode))
    msg = str(ei.value)
    assert "incremental" in msg and "overwrite" in msg and "create_if_missing" in msg


def test_incremental_with_create_if_missing_loads(tmp_path):
    CellConfig(**_cell(tmp_path, mode="create_if_missing"))


def test_overwrite_without_incremental_still_loads(tmp_path):
    CellConfig(**_cell(tmp_path, incremental=False))


def _corpus(tmp_path, names):
    for n in names:
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"body of {n}")


def test_duplicate_stems_raise_with_paths(tmp_path):
    _corpus(tmp_path, ["a/readme.md", "b/readme.md"])
    with pytest.raises(ValueError) as ei:
        Adapter(Cfg(type="files", glob=str(tmp_path / "**/*.md"), id_from="stem"))
    msg = str(ei.value)
    assert "readme" in msg and "a/readme.md" in msg and "b/readme.md" in msg
    assert "id_from: path" in msg


def test_duplicate_stems_ok_with_path_id(tmp_path):
    _corpus(tmp_path, ["a/readme.md", "b/readme.md"])
    ad = Adapter(Cfg(type="files", glob=str(tmp_path / "**/*.md"), id_from="path"))
    ids = [d.id for d in ad.iter_documents()]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_unique_stems_unaffected(tmp_path):
    _corpus(tmp_path, ["a/one.md", "b/two.md"])
    ad = Adapter(Cfg(type="files", glob=str(tmp_path / "**/*.md"), id_from="stem"))
    assert sorted(d.id for d in ad.iter_documents()) == ["one", "two"]


def test_empty_glob_not_raised_at_construction(tmp_path):
    Adapter(Cfg(type="files", glob=str(tmp_path / "*.md"), id_from="stem"))


def test_runner_duplicate_stems_leave_existing_table_untouched(tmp_path):
    pytest.importorskip("sqlite_vec")
    from chunkshop.config import load_config
    from chunkshop.runner import run_cell
    from chunkshop.sinks import load_sink
    from test_runner_files_incremental import _write_cell

    corpus = tmp_path / "corpus"
    (corpus / "a").mkdir(parents=True)
    (corpus / "b").mkdir()
    (corpus / "a" / "readme.md").write_text("Alpha document about cats.")
    (corpus / "b" / "readme.md").write_text("Beta document about dogs.")
    cfg = _write_cell(tmp_path, corpus, tmp_path / "cur.json", incremental=False)
    run_cell(cfg)  # id_from: path -> populates the table with 2 docs

    cfg.source.id_from = "stem"
    cfg.target.mode = "overwrite"
    cfg.target.force_overwrite = True
    res = run_cell(cfg)  # run_cell reports failures via CellResult.error
    assert res.error and "id_from: path" in res.error

    sink = load_sink(cfg.target, embed_dim=cfg.embedder.dim)
    assert sink.count_docs() == 2
