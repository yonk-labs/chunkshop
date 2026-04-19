from chunkshop.config import HttpSource as Cfg
from chunkshop.sources.base import Document


class HttpSource:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def iter_documents(self):
        raise NotImplementedError("HTTP source is not yet implemented; submit an issue to request it")
