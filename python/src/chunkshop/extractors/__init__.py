from chunkshop.config import (
    ExtractorConfig,
    NoneExtractor as NoneCfg,
    RakeKeywordsExtractor as RakeCfg,
)
from chunkshop.extractors.base import Extractor
from chunkshop.extractors.none_provider import NoneExtractor
from chunkshop.extractors.rake_keywords import RakeKeywordsExtractor
from chunkshop.extractors.result import ExtractResult


def load_extractor(cfg: ExtractorConfig) -> Extractor:
    if isinstance(cfg, NoneCfg):
        return NoneExtractor(cfg)
    if isinstance(cfg, RakeCfg):
        return RakeKeywordsExtractor(cfg)
    raise ValueError(f"unknown extractor type: {type(cfg).__name__}")


__all__ = ["Extractor", "ExtractResult", "load_extractor"]
