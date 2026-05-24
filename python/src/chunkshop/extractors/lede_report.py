"""lede_report extractor: structured lede 0.4.5 report facts.

This is the Chunkshop-native path for lede's newer fact/report extraction. It
does not use external dataset caches or domain-specific parsing; it runs on the
chunk text passed to the extractor and stores lede's full JSON ingest payload.
"""
from __future__ import annotations

from typing import Any

from chunkshop.config import LedeReportExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        clean = " ".join(str(item).split())
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _metadata_tags(payload: dict[str, Any], cfg: Cfg) -> list[str]:
    metadata = payload.get("metadata") or {}
    tags: list[str] = []

    if "attributes" in cfg.tag_sources:
        attributes = payload.get("attributes") or {}
        if isinstance(attributes, dict):
            for attr in attributes.values():
                if isinstance(attr, dict):
                    value = attr.get("value")
                    if value is not None:
                        tags.append(str(value))
                elif attr is not None:
                    tags.append(str(attr))
    if "key_facts" in cfg.tag_sources:
        tags.extend(str(fact) for fact in payload.get("key_facts") or [])
    if "fact_records" in cfg.tag_sources:
        for record in payload.get("fact_records") or []:
            if isinstance(record, dict):
                subject = record.get("subject") or ""
                predicate = record.get("predicate") or ""
                obj = record.get("object")
                if obj is not None:
                    tags.append(" ".join(str(part) for part in (subject, predicate, obj) if part))
            elif record is not None:
                tags.append(str(record))
    if "dates" in cfg.tag_sources:
        tags.extend(str(value) for value in metadata.get("dates") or [])
    if "amounts" in cfg.tag_sources:
        tags.extend(str(value) for value in metadata.get("amounts") or [])
    if "entities" in cfg.tag_sources:
        tags.extend(str(value) for value in metadata.get("entities") or [])

    spacy_metadata = payload.get("spacy_metadata") or {}
    if "entities" in cfg.tag_sources and isinstance(spacy_metadata, dict):
        entities = spacy_metadata.get("entities") or []
        if isinstance(entities, dict):
            for values in entities.values():
                tags.extend(str(value) for value in values)
        else:
            tags.extend(str(value) for value in entities)
    if "spacy_phrases" in cfg.tag_sources:
        tags.extend(str(value) for value in payload.get("spacy_phrases") or [])
    if "search_text" in cfg.tag_sources and payload.get("search_text"):
        tags.append(str(payload["search_text"]))

    return [_truncate(tag, cfg.max_tag_chars) for tag in _dedupe(tags)]


class LedeReportExtractor:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def extract(self, text: str) -> ExtractResult:
        try:
            from lede import readable_report
        except ImportError as exc:
            raise RuntimeError(
                "lede_report extractor requires lede>=0.4.5. "
                "Install with: pip install 'lede>=0.4.5' "
                "(or the chunkshop [lede] extra)."
            ) from exc

        report = readable_report(
            text,
            max_length=self.cfg.max_chars,
            max_facts=self.cfg.max_facts,
            backend=self.cfg.backend,
            keep_headings=self.cfg.keep_headings,
            include_toc=self.cfg.include_toc,
        )
        payload = report.to_dict()
        tags = _metadata_tags(payload, self.cfg)
        return ExtractResult(tags=tags, metadata={"lede_report": payload})
