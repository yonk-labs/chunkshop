from chunkshop.config import S3Source as Cfg
from chunkshop.sources.base import Document


class S3Source:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def iter_documents(self):
        raise NotImplementedError("S3 source is not yet implemented; submit an issue to request it")
