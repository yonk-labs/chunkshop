"""RAGFlow Document → chunkshop Document adapter.

The lifted RAGFlow connectors yield instances of
`chunkshop_connectors._base.models.Document` (pydantic BaseModel with
sections, semantic_identifier, blob, doc_updated_at, etc). chunkshop's
pipeline consumes `chunkshop.sources.base.Document` (frozen dataclass:
id, content, title, metadata, fingerprint).

`to_chunkshop_document(rag_doc)` is the surgical mapper between them:

- `rag_doc.sections` (list of TextSection / ImageSection) — joined by
  their `.text` attribute into `content`. RAGFlow's TextSection has
  `text`; ImageSection has no text and contributes an empty string.
- `rag_doc.semantic_identifier` → `title` (falls back to `rag_doc.title`).
- `rag_doc.id` → `id`.
- `rag_doc.metadata` → `metadata`.
- `rag_doc.etag` / `rag_doc.version` / RAGFlow's own `fingerprint`
  attribute → `fingerprint` (first non-None wins).

The function is defensive about shape — connectors lifted in later
tasks may yield duck-typed objects, not full pydantic Document
instances (e.g. lightweight mocks in tests). We use `getattr` with
defaults throughout rather than `.model_dump()`.
"""
from __future__ import annotations

from chunkshop.sources.base import Document


def to_chunkshop_document(rag_doc) -> Document:
    """Map a RAGFlow Document-shaped object onto a chunkshop Document."""
    sections = getattr(rag_doc, "sections", None) or []
    if sections:
        content = "".join(getattr(s, "text", "") or "" for s in sections)
    else:
        content = getattr(rag_doc, "content", "") or ""

    # RAGFlow doesn't have a single canonical fingerprint field across
    # connectors — different lifts attach it as `fingerprint`, `etag`,
    # or `version`. First non-None wins.
    fingerprint = (
        getattr(rag_doc, "fingerprint", None)
        or getattr(rag_doc, "etag", None)
        or getattr(rag_doc, "version", None)
    )

    return Document(
        id=str(rag_doc.id),
        content=content,
        title=getattr(rag_doc, "semantic_identifier", None)
        or getattr(rag_doc, "title", None),
        metadata=getattr(rag_doc, "metadata", None) or None,
        fingerprint=str(fingerprint) if fingerprint is not None else None,
    )
