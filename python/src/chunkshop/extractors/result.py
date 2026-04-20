from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractResult:
    """Return value of an Extractor.

    `tags` is a flat list of strings written to the chunk row's `tags text[]` column.

    `metadata` is merged into each chunk's metadata dict before sink write.
    On key collision with chunker-emitted metadata (e.g. `strategy`, `heading`,
    `start_word`, `n_words`, `neighbor_expand_window`), **the chunker's value
    wins** — chunker metadata is structural provenance, extractor metadata is
    augmentation. Extractors should use unique keys (e.g. `language`,
    `entities`, `keyphrases`) to avoid silent loss.
    """
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
