from pathlib import Path
import textwrap
from chunkshop.sources.files import FilesSource as Adapter
from chunkshop.config import FilesSource as Cfg


def test_files_glob_stem_id(tmp_path):
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    adapter = Adapter(Cfg(type="files", glob=str(tmp_path / "*.md"), id_from="stem"))
    docs = sorted(adapter.iter_documents(), key=lambda d: d.id)
    assert [d.id for d in docs] == ["a", "b"]
    assert docs[0].content == "alpha"


def test_files_empty_glob_raises(tmp_path):
    adapter = Adapter(Cfg(type="files", glob=str(tmp_path / "*.md"), id_from="stem"))
    import pytest
    with pytest.raises(ValueError, match="no files"):
        list(adapter.iter_documents())
