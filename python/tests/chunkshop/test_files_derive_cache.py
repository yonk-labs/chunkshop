from pathlib import Path

import pytest

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


def _src_with_counting_spy(cfg):
    """Build a FilesSource whose "weird" extension is genuinely
    derive-configured (so it lands in ``_derived_exts`` exactly as
    production wiring would), then swap the real deriver for the counting
    spy so the test can observe how many times it actually runs.
    """
    src = FilesSource(cfg)
    src._parsers["weird"] = _Counting()
    return src


def test_second_run_derives_nothing(tmp_path):
    CALLS.clear()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.weird").write_text("x", encoding="utf-8")
    cfg = Cfg(
        type="files",
        glob=str(corpus / "*.weird"),
        derive={"weird": {"rung": "image_meta"}},
        cache={"enabled": True, "root": str(tmp_path / "cache")},
    )

    list(_src_with_counting_spy(cfg).iter_documents())
    assert len(CALLS) == 1

    list(_src_with_counting_spy(cfg).iter_documents())
    assert len(CALLS) == 1, "cache hit must skip the expensive derivation"


def test_disabling_the_cache_always_derives(tmp_path):
    CALLS.clear()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.weird").write_text("x", encoding="utf-8")
    cfg = Cfg(
        type="files",
        glob=str(corpus / "*.weird"),
        derive={"weird": {"rung": "image_meta"}},
        cache={"enabled": False},
    )

    for _ in range(2):
        list(_src_with_counting_spy(cfg).iter_documents())
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


def test_cache_scoped_to_derive_configured_extensions_only(tmp_path):
    """Regression test for the cross-cell encoding-collision bug.

    Before this fix, building a cache for *any* derive block made
    ``_document_for`` route EVERY extension through ``cached_derive`` —
    including plain-text extensions still handled by ``TextParser``, which
    takes a config-dependent ``encoding`` and has no ``derivation_hash()``.
    Its cache key fell back to ``parser.__class__.__name__`` alone (i.e.
    the literal string "TextParser"), identical regardless of encoding, so
    two cells (or two runs with different ``encoding:``) touching the same
    file could silently serve the first cell's decoded text to the second.

    Only the derive-configured extension (".png" here) may produce a cache
    entry; the plain-text ".txt" file, dispatched to the default
    ``TextParser``, must bypass the cache entirely.
    """
    Image = pytest.importorskip("PIL.Image", reason="Pillow comes with fastembed")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    Image.new("RGB", (2, 2), "red").save(corpus / "a.png")
    (corpus / "b.txt").write_text("hello world", encoding="utf-8")
    cache_root = tmp_path / "cache"
    cfg = Cfg(
        type="files",
        glob=str(corpus / "*"),
        derive={"png": {"rung": "image_meta"}},
        cache={"root": str(cache_root)},
    )

    docs = list(FilesSource(cfg).iter_documents())
    assert len(docs) == 2

    cache_files = list(cache_root.rglob("*.json"))
    assert len(cache_files) == 1, (
        "only the derive-configured .png should produce a cache entry; "
        "the plain TextParser-handled .txt must bypass the cache entirely"
    )


def test_pipe_separated_keys_with_whitespace_and_leading_dots(tmp_path):
    """Regression coverage for pipe-key parsing: whitespace around each
    segment and an optional leading dot must both be tolerated, and every
    resulting extension must be marked derive-configured (cache-eligible).
    """
    from chunkshop.sources.derivers.image_meta import ImageMetaDeriver

    cfg = Cfg(
        type="files",
        glob=str(tmp_path / "**" / "*"),
        derive={" .PNG | jpg |webp ": {"rung": "image_meta"}},
    )
    src = FilesSource(cfg)

    assert src._derived_exts == {"png", "jpg", "webp"}
    for ext in ("png", "jpg", "webp"):
        assert isinstance(src._parsers[ext], ImageMetaDeriver)


def test_fingerprint_asymmetry_on_full_resync(tmp_path):
    """Pins the intended, currently-shipped contract on the full-resync
    (``iter_documents``) path: ``Document.fingerprint`` is populated only
    where the cache actually needs a content hash.

    This asymmetry is intentional, not an oversight:

    - A non-derived extension takes the plain ``call_deriver`` path (no
      cache lookup possible), so no content hash is computed and
      ``fingerprint`` stays ``None`` — matching shipped ``main`` (v1.0.0-rc6,
      ``3df86cb``), where ``iter_documents`` has *always* called
      ``_document_for(Path(p))`` with no ``content_hash``, full stop. This
      task's first commit briefly changed that unconditionally; that was a
      regression, not a design decision, and was reverted here.
    - A derive-configured extension needs the hash for its cache key, so it
      gets computed regardless — and the resulting ``Document`` now
      *incidentally* carries a fingerprint it never had before this task.
      That's a strict improvement, not a behaviour change anyone asked for.

    The incremental path (``iter_changes_since``) is unaffected either way:
    it always computes and passes ``content_hash`` itself before calling
    ``_document_for``, for every extension, derived or not — already
    covered by ``test_full_resync_from_empty_cursor_sets_fingerprint`` in
    test_files_incremental.py (non-derived ``.md``, via
    ``iter_changes_since``), so it is not re-asserted here.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text("hello", encoding="utf-8")
    Image = pytest.importorskip("PIL.Image", reason="Pillow comes with fastembed")
    Image.new("RGB", (2, 2), "red").save(corpus / "b.png")

    cfg = Cfg(
        type="files",
        glob=str(corpus / "*"),
        derive={"png": {"rung": "image_meta"}},
        cache={"root": str(tmp_path / "cache")},
    )

    docs = {Path(d.metadata["source_path"]).suffix.lstrip("."): d
            for d in FilesSource(cfg).iter_documents()}

    assert docs["txt"].fingerprint is None, (
        "non-derived extension on full resync: no cache lookup needed, so "
        "no content hash is computed — matches shipped main's long-standing "
        "iter_documents() behaviour"
    )
    assert docs["png"].fingerprint is not None, (
        "derive-configured extension on full resync: the cache needs a "
        "content hash for its key, so the Document incidentally gets a "
        "fingerprint it never had pre-media-ingest"
    )
