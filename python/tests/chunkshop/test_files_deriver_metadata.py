from pathlib import Path

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers.result import ParseResult


class _MetaDeriver:
    supported_extensions = ["weird"]

    def derive(self, path: Path) -> ParseResult:
        return ParseResult(
            text="body text",
            metadata={"width": 8, "height": 8, "source_path": "SHOULD_NOT_WIN"},
        )


def test_deriver_metadata_reaches_document(tmp_path):
    (tmp_path / "a.weird").write_text("x", encoding="utf-8")
    src = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.weird")),
        parsers={"weird": _MetaDeriver()},
    )
    doc = list(src.iter_documents())[0]
    assert doc.content == "body text"
    assert doc.metadata["width"] == 8
    assert doc.metadata["height"] == 8


def test_source_keys_win_over_deriver_keys(tmp_path):
    """source_path is chunkshop's provenance, not the deriver's to overwrite."""
    (tmp_path / "a.weird").write_text("x", encoding="utf-8")
    src = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.weird")),
        parsers={"weird": _MetaDeriver()},
    )
    doc = list(src.iter_documents())[0]
    assert doc.metadata["source_path"] == str(tmp_path / "a.weird")
