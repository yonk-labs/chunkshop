"""Go symbol + call-site extraction.

v1 is regex-only: tree-sitter-go ships pre-built wheels but the marginal
value over the regex fallback for Go's relatively flat syntax is low, so
we keep it out of the ``[code]`` extra to avoid bloating installs. This
module exists so a future tree-sitter-go path has a home — for now it
forwards to :mod:`chunkshop.codeparse.langs.regex_fallback`.
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
    """Decode bytes to text and run the regex extractor.

    The bytes→text decode tolerates malformed UTF-8 the same way the
    tree-sitter wrappers do — matching behaviour across all language
    modules is what makes the per-language layer interchangeable.
    """
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        text = source.decode("latin-1")
    return regex_fallback.extract_with_regex(
        text=text,
        language="go",
        file_path=file_path,
        project_id=project_id,
    )


__all__ = ["parse"]
