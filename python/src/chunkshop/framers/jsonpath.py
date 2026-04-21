from __future__ import annotations
import json
from dataclasses import replace

from chunkshop.config import JSONPathFramerConfig
from chunkshop.sources.base import Document


def _walk(obj, parts: list[str]) -> list:
    """Traverse dotted path with '*' for list iteration. Returns flat list."""
    if not parts:
        return [obj]
    head, *rest = parts
    if head == "*":
        if not isinstance(obj, list):
            return []
        out = []
        for item in obj:
            out.extend(_walk(item, rest))
        return out
    if isinstance(obj, dict) and head in obj:
        return _walk(obj[head], rest)
    return []


class JSONPathFramer:
    """Parse raw.content as JSON; walk a dotted path (with '*' for list iteration);
    emit one framed Document per element.
    """

    def __init__(self, cfg: JSONPathFramerConfig):
        self.cfg = cfg
        self._row_parts = cfg.row_path.split(".") if cfg.row_path != "$" else []
        self._body_parts = cfg.body_path.split(".") if cfg.body_path != "$" else []
        self._title_parts = cfg.title_path.split(".") if cfg.title_path else None

    def frame(self, raw: Document) -> list[Document]:
        try:
            obj = json.loads(raw.content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONPathFramer: raw.content is not valid JSON: {exc}")
        rows = _walk(obj, self._row_parts) if self._row_parts else [obj]
        frames: list[Document] = []
        for row in rows:
            body_values = _walk(row, self._body_parts) if self._body_parts else [row]
            if not body_values:
                continue
            body = body_values[0]
            if not isinstance(body, str):
                body = json.dumps(body)
            title = raw.title
            if self._title_parts:
                tvs = _walk(row, self._title_parts)
                if tvs and isinstance(tvs[0], str):
                    title = tvs[0]
            meta = dict(raw.metadata or {})
            meta["framer"] = "jsonpath"
            meta["frame_seq"] = len(frames)
            frames.append(replace(
                raw,
                id=f"{raw.id}#{len(frames)}",
                content=body,
                title=title,
                metadata=meta,
            ))
        return frames
