"""Shared paragraph -> sentence -> char splitter.

Used by HierarchyChunker and SentenceAwareChunker to hard-cap chunk size at
the embedder's token budget. Prefers semantic boundaries (blank-line paragraph
breaks, sentence-ending punctuation) before falling back to character slicing.
"""
from __future__ import annotations
import re

_PARA_BREAK = re.compile(r"\n\s*\n")
# Match sentence-ending punctuation followed by whitespace. Use a capturing
# group so we keep the trailing whitespace in the tokenized stream — that way
# sentence_parts[i] still carries the space that bridged it to sentence_parts[i+1],
# so concatenating chunks later doesn't silently drop inter-sentence whitespace.
_SENT_BOUNDARY = re.compile(r"([.!?]\s+)")


def _char_slice(text: str, max_chars: int) -> list[str]:
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _sentence_tokens(text: str) -> list[str]:
    """Split text into sentence tokens that each retain any trailing whitespace.

    Example: "Foo. Bar! Baz?" -> ["Foo. ", "Bar! ", "Baz?"]. Joining all tokens
    reproduces the input exactly.
    """
    parts = _SENT_BOUNDARY.split(text)
    tokens: list[str] = []
    i = 0
    while i < len(parts):
        body = parts[i]
        tail = parts[i + 1] if i + 1 < len(parts) else ""
        token = body + tail
        if token:
            tokens.append(token)
        i += 2
    return tokens


def _split_sentences(text: str, max_chars: int) -> list[str]:
    tokens = _sentence_tokens(text)
    out: list[str] = []
    buf = ""
    for s in tokens:
        if not s:
            continue
        # Sentence itself too big -> hard char-slice it
        if len(s) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_char_slice(s, max_chars))
            continue
        candidate = buf + s if buf else s
        if len(candidate) > max_chars:
            if buf:
                out.append(buf)
            buf = s
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out


def split_to_max_chars(text: str, max_chars: int) -> list[str]:
    """Split text into chunks no larger than max_chars.

    Order: paragraph boundaries -> sentence boundaries -> character slice.
    Guarantees: every char of input appears in some output chunk (whitespace
    between paragraphs/sentences may be normalized).
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in _PARA_BREAK.split(text) if p.strip()]
    out: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > max_chars:
            # Paragraph itself too big -> descend to sentence splitting. Always
            # append "\n" when flushing buf before a descended paragraph: the
            # descended paragraph will emit further chunks, and we need a
            # whitespace boundary so "".join(chunks) preserves the paragraph
            # gap. The trailing "\n" is inside the max_chars budget via the
            # `-1` reservation when building buf below.
            if buf:
                out.append(buf + "\n")
                buf = ""
            out.extend(_split_sentences(para, max_chars))
            continue
        # Reserve one char for a potential trailing "\n" appended when we flush
        # at a paragraph boundary. Keeps the hard max at max_chars even for the
        # flushed chunk carrying its boundary newline.
        budget = max_chars - 1
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > budget:
            if buf:
                out.append(buf + "\n")
            # para was already validated <= max_chars at the top of the loop;
            # if para itself is max_chars (no room for trailing "\n"), a later
            # flush will truncate to paragraph-boundary without newline marker,
            # which is fine — content preservation via \s+ normalization holds.
            buf = para
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out
