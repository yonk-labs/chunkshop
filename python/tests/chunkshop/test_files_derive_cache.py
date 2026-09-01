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
        # A derive block (for an unrelated extension) is required to build a
        # cache at all — see test_no_derive_block_builds_no_cache below. The
        # injected `parsers={"weird": _Counting()}` below still wins over
        # this block for the "weird" extension the corpus actually uses.
        derive={"png": {"rung": "image_meta"}},
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
    cfg = Cfg(
        type="files",
        glob=str(corpus / "*.weird"),
        derive={"png": {"rung": "image_meta"}},
        cache={"enabled": False},
    )

    for _ in range(2):
        list(FilesSource(cfg, parsers={"weird": _Counting()}).iter_documents())
    assert len(CALLS) == 2


def test_no_derive_block_builds_no_cache(tmp_path):
    """NN-6: text-only users pay nothing — no cache directory, no disk writes.

    A cell with no `derive` block (the ordinary text-ingest case) must not
    construct a DerivationCache at all, even when `cache.enabled` defaults to
    True. Without this gate, indexing markdown would silently create
    ~/.cache/chunkshop/derived/ (or whatever `cache.root` points at) purely
    from cheap TextParser calls that never needed memoising.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("hello world", encoding="utf-8")
    cache_root = tmp_path / "cache"
    cfg = Cfg(
        type="files",
        glob=str(corpus / "*.md"),
        cache={"root": str(cache_root)},
    )

    src = FilesSource(cfg)
    assert src._cache is None

    docs = list(src.iter_documents())
    assert len(docs) == 1
    assert not cache_root.exists(), "no derive block means no cache directory should be created"


def test_derive_block_still_caches_by_default(tmp_path):
    """The other half of the gate: a media cell gets caching for free."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.weird").write_text("x", encoding="utf-8")
    cache_root = tmp_path / "cache"
    cfg = Cfg(
        type="files",
        glob=str(corpus / "*.weird"),
        derive={"png": {"rung": "image_meta"}},
        cache={"root": str(cache_root)},
    )

    src = FilesSource(cfg, parsers={"weird": _Counting()})
    assert src._cache is not None
