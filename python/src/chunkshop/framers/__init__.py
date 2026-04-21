from chunkshop.framers.base import DocFramer
from chunkshop.framers.identity import IdentityFramer
from chunkshop.framers.heading_boundary import HeadingBoundaryFramer


def load_framer(cfg) -> DocFramer:
    from chunkshop.config import IdentityFramerConfig, HeadingBoundaryFramerConfig

    if isinstance(cfg, IdentityFramerConfig):
        return IdentityFramer()
    if isinstance(cfg, HeadingBoundaryFramerConfig):
        return HeadingBoundaryFramer(cfg)
    raise ValueError(f"unknown framer type: {type(cfg).__name__}")


__all__ = ["DocFramer", "IdentityFramer", "HeadingBoundaryFramer", "load_framer"]
