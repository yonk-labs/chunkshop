from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractResult:
    """Return value of an Extractor. `tags` is a flat list for the text[] column;
    `metadata` is a dict merged into each chunk's metadata jsonb.
    """
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
