"""The value a deriver returns: extracted text plus whatever metadata the
derivation produced (dimensions, duration, model provenance, ...).

Derivers may return a bare ``str`` — the historical ``FileParser`` contract —
and ``normalize_parse_result`` wraps it. This is what keeps third-party
parsers written against chunkshop 1.0 working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParseResult:
    text: str
    metadata: dict = field(default_factory=dict)


def normalize_parse_result(value: str | ParseResult) -> ParseResult:
    """Coerce a deriver's return value to a ParseResult.

    A bare ``str`` is the legacy ``FileParser.parse`` contract and is wrapped
    with empty metadata.
    """
    if isinstance(value, ParseResult):
        return value
    if isinstance(value, str):
        return ParseResult(text=value)
    raise TypeError(
        f"deriver returned {type(value).__name__}; expected str or ParseResult"
    )
