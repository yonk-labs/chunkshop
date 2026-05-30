"""Multi-language parse driver with tree-sitter + regex fallback.

This is the public entry point. It owns three responsibilities:

1. **Language detection** (by file extension) when the caller doesn't pass
   ``language`` explicitly.
2. **Per-language dispatch** to the right module under :mod:`chunkshop.
   codeparse.langs`. Each language module imports its tree-sitter package
   lazily on first ``parse()`` call, so plain ``import chunkshop.codeparse``
   does NOT pull in tree-sitter — the heavy native wheels stay dormant
   until a file is actually parsed.
3. **Fallback to regex** when the tree-sitter package isn't installed or
   when the parser raises. This is the contract that keeps the ``[code]``
   extra optional: if you don't install ``tree-sitter-python`` you still
   get a non-empty ParseResult from any Python file.

The wrapper is intentionally tiny — the language-specific knowledge lives
in the per-language modules so that adding a new language is one file +
one ``elif`` here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from chunkshop.codeparse.base import ParseResult
from chunkshop.codeparse.langs import regex_fallback

logger = logging.getLogger(__name__)


def parse_file(
    path: Path | str,
    language: Optional[str] = None,
    *,
    project_id: str = "default",
) -> ParseResult:
    """Parse a source file and return its ParseResult.

    ``language`` is one of {"python", "java", "go", "typescript",
    "javascript", "rust", "c", "cpp", "csharp", "ruby"} or None
    (auto-detect from suffix). Unknown languages return an empty
    ParseResult — not an error — so an ingest pipeline can hand off any
    file blindly without pre-filtering.

    The function NEVER raises on a missing tree-sitter package, malformed
    UTF-8, or a parser crash. Every failure mode falls through to the
    regex extractor, which is itself best-effort. A pipeline error here
    is reserved for actual I/O failures (the file disappeared between
    detection and read).
    """
    file_path = Path(path)
    if not file_path.is_file():
        logger.warning("parse_file: %s does not exist", file_path)
        return ParseResult()

    lang = language or regex_fallback.detect_language(file_path)
    if lang is None:
        # Unknown extension: hand back an empty result rather than guessing.
        return ParseResult()

    try:
        source = file_path.read_bytes()
    except OSError as exc:  # pragma: no cover — file I/O failure
        logger.warning("parse_file: could not read %s: %s", file_path, exc)
        return ParseResult(language=lang)

    return _dispatch(
        source=source,
        file_path=str(file_path),
        language=lang,
        project_id=project_id,
    )


def parse_text(
    content: str,
    *,
    language: str,
    file_path: str = "<text>",
    project_id: str = "default",
) -> ParseResult:
    """Parse a source string and return its ParseResult.

    The in-memory companion to :func:`parse_file`. Used by the
    ``code_relationships`` extractor (SP-C), which receives chunk-body
    strings from the runner and needs to re-parse them without round-
    tripping through a tempfile.

    ``language`` is mandatory here — without a path to detect from, the
    caller must say what they're parsing. Unknown languages return an
    empty ``ParseResult`` rather than raising, matching
    :func:`parse_file`'s posture.
    """
    if language not in _SUPPORTED_LANGUAGES:
        return ParseResult(language=language)
    if isinstance(content, str):
        source = content.encode("utf-8")
    else:  # pragma: no cover — defensive: callers pass strings
        source = bytes(content)
    return _dispatch(
        source=source,
        file_path=file_path,
        language=language,
        project_id=project_id,
    )


_SUPPORTED_LANGUAGES = frozenset(
    {
        "python", "java", "go", "typescript", "javascript",
        "rust", "c", "cpp", "csharp", "ruby",
    }
)


def _dispatch(
    *,
    source: bytes,
    file_path: str,
    language: str,
    project_id: str,
) -> ParseResult:
    """Pick the right per-language extractor and fall back on failure.

    Wrapped in a try/except so that a tree-sitter import error, a grammar
    bug, or any other surprise doesn't sink the cell. The regex fallback
    is always the safety net.
    """
    try:
        if language == "python":
            from chunkshop.codeparse.langs import python as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "java":
            from chunkshop.codeparse.langs import java as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "go":
            from chunkshop.codeparse.langs import go as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "typescript":
            from chunkshop.codeparse.langs import typescript as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "javascript":
            from chunkshop.codeparse.langs import javascript as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "rust":
            from chunkshop.codeparse.langs import rust as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "c":
            from chunkshop.codeparse.langs import c as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "cpp":
            from chunkshop.codeparse.langs import cpp as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "csharp":
            from chunkshop.codeparse.langs import csharp as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
        if language == "ruby":
            from chunkshop.codeparse.langs import ruby as lang_mod

            return lang_mod.parse(
                source=source, file_path=file_path, project_id=project_id
            )
    except ImportError as exc:
        logger.info(
            "tree-sitter package missing for %s (%s); falling back to regex",
            language,
            exc,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "tree-sitter parse failed for %s (%s); falling back to regex",
            file_path,
            exc,
        )

    # Universal fallback: regex extractor handles every language we know
    # about. Decode tolerantly because the file may not be valid UTF-8.
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        text = source.decode("latin-1")
    return regex_fallback.extract_with_regex(
        text=text,
        language=language,
        file_path=file_path,
        project_id=project_id,
    )


__all__ = ["parse_file", "parse_text"]
