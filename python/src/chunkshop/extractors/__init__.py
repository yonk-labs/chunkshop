from chunkshop.config import (
    ExtractorConfig,
    LangDetectExtractor as LangDetectCfg,
    NoneExtractor as NoneCfg,
    RakeKeywordsExtractor as RakeCfg,
)
from chunkshop.extractors.base import Extractor
from chunkshop.extractors.lang_detect import LangDetectExtractor
from chunkshop.extractors.none_provider import NoneExtractor
from chunkshop.extractors.rake_keywords import RakeKeywordsExtractor
from chunkshop.extractors.result import ExtractResult


def load_extractor(cfg: ExtractorConfig) -> Extractor:
    if isinstance(cfg, NoneCfg):
        return NoneExtractor(cfg)
    if isinstance(cfg, RakeCfg):
        return RakeKeywordsExtractor(cfg)
    if isinstance(cfg, LangDetectCfg):
        return LangDetectExtractor(cfg)
    raise ValueError(f"unknown extractor type: {type(cfg).__name__}")


__all__ = ["Extractor", "ExtractResult", "load_extractor"]
