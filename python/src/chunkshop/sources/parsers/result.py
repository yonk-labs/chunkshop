"""The value a deriver returns: extracted text plus whatever metadata the
derivation produced (dimensions, duration, model provenance, ...).

Derivers may return a bare ``str`` — the historical ``FileParser`` contract —
and ``normalize_parse_result`` wraps it. This is what keeps third-party
parsers written against chunkshop 1.0 working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ParseResult:
    text: str
    metadata: dict = field(default_factory=dict)
    #: Evidence that this file DEMONSTRABLY held content the parser failed to
    #: turn into text — a human phrase naming what it found ("1 page(s)").
    #:
    #: This is the F5 signal, and it is deliberately an AFFIRMATIVE CLAIM by
    #: the parser rather than something ``FilesSource`` infers from an empty
    #: ``text``. The two are not the same fact, and conflating them is the
    #: regression this field exists to make impossible: a zero-byte or
    #: whitespace-only ``.md`` is LEGITIMATELY empty — bytes in, no text out,
    #: nothing lost — while a PDF with pages and no characters is a failed
    #: extraction. ``TextParser`` has no notion of "content units" at all and
    #: therefore can never set this, so no amount of empty text on the plain
    #: path can reach the loud branch.
    #:
    #: A parser sets it only when it both (a) counted at least one content
    #: unit it knows how to read (a PDF page, a DOCX table, a slide, an
    #: embedded HTML element) and (b) produced no characters. ``None`` — the
    #: default, and the right answer for any parser with no honest way to
    #: tell the two cases apart — makes no claim either way.
    had_content: Optional[str] = None


def normalize_parse_result(value: str | ParseResult) -> ParseResult:
    """Coerce a deriver's return value to a ParseResult.

    A bare ``str`` is the legacy ``FileParser.parse`` contract and is wrapped
    with empty metadata — and with no ``had_content`` claim, which is the
    conservative answer for a third-party parser that predates the field.
    """
    if isinstance(value, ParseResult):
        return value
    if isinstance(value, str):
        return ParseResult(text=value)
    raise TypeError(
        f"deriver returned {type(value).__name__}; expected str or ParseResult"
    )
