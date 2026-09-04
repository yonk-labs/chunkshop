"""`on_empty` is a DERIVATION policy, scoped to derive-routed documents.

Two facts this module pins, and they pull in opposite directions:

1. A document routed through the ``derive:`` block that yields no text
   honours ``on_empty`` in all three modes. That is the whole point of the
   setting — a scanned image that OCRs to nothing is a failure, not an
   empty success.
2. A document handled by an ordinary parser is NOT subject to it merely
   for coming out empty. A zero-byte or whitespace-only ``.md`` is indexed
   as an empty document, exactly as it was before ``on_empty`` existed.
   Applying (1) to (2) was a data-loss regression: the default ``error``
   aborted the run on any text-only corpus containing an empty file, and it
   aborted it *after* ``target.mode: overwrite`` had already dropped the
   populated table.

There is now a third route, and it does not weaken (2). A parser may make an
affirmative ``ParseResult.had_content`` claim — "I counted pages/tables/
slides and produced no characters" — and that reaches ``on_empty`` too. See
``test_parser_empty_derivation_f5.py``, which owns that case. ``TextParser``
has no notion of a content unit and never makes the claim, so (2) holds
whatever the file contains.
"""

from contextlib import contextmanager
from pathlib import Path

import pytest

from chunkshop.config import FilesSource as Cfg
from chunkshop.config import ImageMetaDeriverConfig
from chunkshop.sources.files import FilesSource
from chunkshop.sources.parsers.base import EmptyDerivationError


class _EmptyDeriver:
    """Stands in for a real rung that derives nothing (a blank scan)."""

    supported_extensions = ["blank"]

    def derive(self, path: Path) -> str:
        return "   \n  "  # whitespace only — as good as empty


@contextmanager
def _routed_to(deriver):
    """Make the ``derive:`` block resolve to ``deriver``.

    The gate under test is the real derive-routing signal — an extension
    claimed by ``cfg.derive`` — so a test deriver has to reach the source
    through that block, not through the ``parsers=`` injection hatch. The
    rung named in the config is irrelevant; only the routing is.
    """
    import chunkshop.sources.derivers as derivers_mod

    original = derivers_mod.load_deriver
    derivers_mod.load_deriver = lambda cfg: deriver
    try:
        yield
    finally:
        derivers_mod.load_deriver = original


def _derived_src(tmp_path, **kw):
    """A source whose ``.blank`` files are genuinely derive-routed."""
    (tmp_path / "a.blank").write_text("x", encoding="utf-8")
    kw.setdefault("cache", {"enabled": False})
    with _routed_to(_EmptyDeriver()):
        return FilesSource(
            Cfg(
                type="files",
                glob=str(tmp_path / "*.blank"),
                derive={"blank": ImageMetaDeriverConfig(rung="image_meta")},
                **kw,
            )
        )


# --- (1) the derive path: on_empty still governs, in all three modes -----


def test_empty_derivation_raises_by_default(tmp_path):
    with pytest.raises(EmptyDerivationError, match="a.blank"):
        list(_derived_src(tmp_path).iter_documents())


def test_on_empty_skip_omits_the_document(tmp_path):
    assert list(_derived_src(tmp_path, on_empty="skip").iter_documents()) == []


def test_on_empty_index_preserves_legacy_behaviour(tmp_path):
    docs = list(_derived_src(tmp_path, on_empty="index").iter_documents())
    assert len(docs) == 1
    assert docs[0].content.strip() == ""


def test_derive_configured_error_message_offers_ocr_hint(tmp_path):
    """A derive-routed failure gets the media-specific OCR hint."""
    with pytest.raises(EmptyDerivationError) as exc_info:
        list(_derived_src(tmp_path).iter_documents())
    message = str(exc_info.value)
    assert "ocr" in message.lower()
    # Still actionable: names the other two modes by name.
    assert "skip" in message
    assert "index" in message


def test_on_empty_skip_also_applies_to_the_incremental_path(tmp_path):
    """iter_changes_since must not yield None for a skipped document."""
    src = _derived_src(
        tmp_path,
        on_empty="skip",
        incremental={"cursor_path": str(tmp_path / "cursor.json")},
    )
    docs = list(src.iter_changes_since(src.empty_cursor()))
    assert docs == [], "no None may leak into the runner"


def test_derive_routed_pdf_with_no_text_still_raises(tmp_path):
    """A PDF the user explicitly routed to a rung is a derivation failure."""
    (tmp_path / "scanned.pdf").write_bytes(b"%PDF-1.4\n")
    with _routed_to(_EmptyDeriver()):
        src = FilesSource(
            Cfg(
                type="files",
                glob=str(tmp_path / "*.pdf"),
                derive={"pdf": ImageMetaDeriverConfig(rung="image_meta")},
                cache={"enabled": False},
            )
        )
    with pytest.raises(EmptyDerivationError, match="scanned.pdf"):
        list(src.iter_documents())


# --- (2) the plain-parser path: on_empty must never fire ----------------


def _text_corpus(tmp_path):
    """One normal, one zero-byte, one whitespace-only. No `derive:` anywhere."""
    (tmp_path / "doc-normal.md").write_text("# Heading\n\nreal prose\n", encoding="utf-8")
    (tmp_path / "doc-empty.md").write_text("", encoding="utf-8")
    (tmp_path / "doc-ws.md").write_text("   \n\n\t\n", encoding="utf-8")
    return FilesSource(Cfg(type="files", glob=str(tmp_path / "*.md")))


def test_text_only_corpus_with_empty_files_ingests_under_default_config(tmp_path):
    """THE regression test. Default config, no derive block, empty .md files.

    Before the fix this raised EmptyDerivationError on ``doc-empty.md`` and
    the run aborted with zero documents — after the sink had already dropped
    the target table under ``mode: overwrite``.
    """
    docs = list(_text_corpus(tmp_path).iter_documents())
    assert len(docs) == 3, [d.id for d in docs]
    by_id = {d.id: d for d in docs}
    assert by_id["doc-normal"].content.strip()
    assert by_id["doc-empty"].content == ""
    assert by_id["doc-ws"].content.strip() == ""


@pytest.mark.parametrize("mode", ["error", "skip", "index"])
def test_text_only_corpus_is_unaffected_by_every_on_empty_mode(tmp_path, mode):
    """`on_empty` has no reach over the plain-parser path, whatever it says.

    ``skip`` included: a user who set it for their images must not silently
    lose empty text documents from the same cell.
    """
    (tmp_path / "doc-normal.md").write_text("# H\n\nprose\n", encoding="utf-8")
    (tmp_path / "doc-empty.md").write_text("", encoding="utf-8")
    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.md"), on_empty=mode))
    assert len(list(src.iter_documents())) == 2


def test_pdf_with_no_extractable_text_now_raises(tmp_path):
    """INVERTED, deliberately — this is spec defect F5 being closed.

    Was ``test_pdf_with_no_extractable_text_is_indexed_not_raised``, which
    asserted that a PDF nobody routed to a rung indexed as a silent empty
    document. That WAS the defect: F5 requires "a parser yielding no
    derivation raises or records a failure state; never a silently-indexed
    empty doc". `PDFParser` now reports the page count alongside the empty
    text, and `FilesSource` raises on it by default.

    This is a real behaviour change from 1.0.0-rc6 and is narrowly targeted:
    the trigger is the parser's affirmative claim, not empty text, so the
    plain-text guarantee two tests up is untouched. `on_empty: skip` /
    `index` remain the way through — see `test_parser_empty_derivation_f5.py`.
    """
    pytest.importorskip("pypdf")
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with (tmp_path / "scanned.pdf").open("wb") as fh:
        writer.write(fh)

    src = FilesSource(Cfg(type="files", glob=str(tmp_path / "*.pdf")))
    with pytest.raises(EmptyDerivationError, match=r"1 page\(s\)"):
        list(src.iter_documents())

    # ...and the escape hatch still lets it through.
    lenient = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.pdf"), on_empty="index")
    )
    docs = list(lenient.iter_documents())
    assert len(docs) == 1
    assert docs[0].content.strip() == ""


def test_injected_parser_is_not_derive_routed(tmp_path):
    """`parsers=` is a parser-table override, not a `derive:` block.

    A custom object that happens to implement `derive()` must not opt a cell
    into the derivation policy behind the config's back — only ``derive:``
    does that. Guards against re-deriving "was this derived" from the parser
    object rather than from the routing table.
    """
    (tmp_path / "a.blank").write_text("x", encoding="utf-8")
    src = FilesSource(
        Cfg(type="files", glob=str(tmp_path / "*.blank")),
        parsers={"blank": _EmptyDeriver()},
    )
    docs = list(src.iter_documents())
    assert len(docs) == 1
    assert docs[0].content.strip() == ""
