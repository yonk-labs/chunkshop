from pathlib import Path

from chunkshop.config import FilesSource as Cfg
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers.result import ParseResult

CALLS = []


class _Counting:
    supported_extensions = ["weird"]

    def derivation_hash(self) -> str:
        return "fixed-hash-v1"

    def derive(self, path: Path) -> ParseResult:
        CALLS.append(str(path))
        return ParseResult(text="expensive", metadata={"k": 1})


def test_second_run_derives_nothing(tmp_path):
    CALLS.clear()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.weird").write_text("x", encoding="utf-8")
    cfg = Cfg(
        type="files",
        glob=str(corpus / "*.weird"),
        cache={"enabled": True, "root": str(tmp_path / "cache")},
    )

    list(FilesSource(cfg, parsers={"weird": _Counting()}).iter_documents())
    assert len(CALLS) == 1

    list(FilesSource(cfg, parsers={"weird": _Counting()}).iter_documents())
    assert len(CALLS) == 1, "cache hit must skip the expensive derivation"


def test_disabling_the_cache_always_derives(tmp_path):
    CALLS.clear()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.weird").write_text("x", encoding="utf-8")
    cfg = Cfg(type="files", glob=str(corpus / "*.weird"), cache={"enabled": False})

    for _ in range(2):
        list(FilesSource(cfg, parsers={"weird": _Counting()}).iter_documents())
    assert len(CALLS) == 2
