from chunkshop.config import (
    CompositeExtractor as CompositeCfg,
    ExtractorConfig,
    KeyBertPhrasesExtractor as KeyBertCfg,
    LangDetectExtractor as LangDetectCfg,
    LedeReportExtractor as LedeReportCfg,
    LedeTopTermsExtractor as LedeTopTermsCfg,
    NoneExtractor as NoneCfg,
    RakeKeywordsExtractor as RakeCfg,
    SpacyEntitiesExtractor as SpacyCfg,
)
from chunkshop.extractors.base import Extractor
from chunkshop.extractors.composite import CompositeExtractor
from chunkshop.extractors.keybert_phrases import KeyBertPhrasesExtractor
from chunkshop.extractors.lang_detect import LangDetectExtractor
from chunkshop.extractors.lede_report import LedeReportExtractor
from chunkshop.extractors.lede_top_terms import LedeTopTermsExtractor
from chunkshop.extractors.none_provider import NoneExtractor
from chunkshop.extractors.rake_keywords import RakeKeywordsExtractor
from chunkshop.extractors.result import ExtractResult
from chunkshop.extractors.spacy_entities import SpacyEntitiesExtractor


def load_extractor(cfg: ExtractorConfig) -> Extractor:
    if isinstance(cfg, NoneCfg):
        return NoneExtractor(cfg)
    if isinstance(cfg, RakeCfg):
        return RakeKeywordsExtractor(cfg)
    if isinstance(cfg, LangDetectCfg):
        return LangDetectExtractor(cfg)
    if isinstance(cfg, KeyBertCfg):
        return KeyBertPhrasesExtractor(cfg)
    if isinstance(cfg, SpacyCfg):
        return SpacyEntitiesExtractor(cfg)
    if isinstance(cfg, LedeTopTermsCfg):
        return LedeTopTermsExtractor(cfg)
    if isinstance(cfg, LedeReportCfg):
        return LedeReportExtractor(cfg)
    if isinstance(cfg, CompositeCfg):
        return CompositeExtractor(cfg)
    raise ValueError(f"unknown extractor type: {type(cfg).__name__}")


__all__ = ["Extractor", "ExtractResult", "load_extractor"]
