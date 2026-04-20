from chunkshop.config import NoneExtractor as Cfg
from chunkshop.extractors.result import ExtractResult


class NoneExtractor:
    def __init__(self, cfg: Cfg | None = None):
        self.cfg = cfg

    def extract(self, text: str) -> ExtractResult:
        return ExtractResult()
