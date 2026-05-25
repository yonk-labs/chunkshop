"""TypeScript symbol + call-site extraction.

v1 is regex-only — see :mod:`chunkshop.codeparse.langs.go` for the
rationale on staying out of the ``[code]`` extra for languages where
regex coverage is adequate. The regex fallback for typescript handles
classes, interfaces, functions, and methods.
"""
from __future__ import annotations

from chunkshop.codeparse.base import ParseResult
from chunkshop.codeparse.langs import regex_fallback


def parse(
    *,
    source: bytes,
    file_path: str,
    project_id: str = "default",
) -> ParseResult:
    """Decode bytes to text and run the regex extractor."""
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        text = source.decode("latin-1")
    return regex_fallback.extract_with_regex(
        text=text,
        language="typescript",
        file_path=file_path,
        project_id=project_id,
    )


__all__ = ["parse"]
