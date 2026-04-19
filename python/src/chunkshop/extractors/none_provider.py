from chunkshop.config import NoneExtractor as Cfg


class NoneExtractor:
    def __init__(self, cfg: Cfg | None = None):
        self.cfg = cfg

    def extract(self, text: str) -> list[str]:
        return []
