from chunkshop.framers.base import DocFramer
from chunkshop.framers.identity import IdentityFramer


def load_framer(cfg) -> DocFramer:
    """Factory. Expanded as more framers land in later tasks."""
    from chunkshop.config import IdentityFramerConfig

    if isinstance(cfg, IdentityFramerConfig):
        return IdentityFramer()
    raise ValueError(f"unknown framer type: {type(cfg).__name__}")


__all__ = ["DocFramer", "IdentityFramer", "load_framer"]
