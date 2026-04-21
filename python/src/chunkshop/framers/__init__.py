from chunkshop.framers.base import DocFramer
from chunkshop.framers.identity import IdentityFramer
from chunkshop.framers.heading_boundary import HeadingBoundaryFramer
from chunkshop.framers.regex_boundary import RegexBoundaryFramer


def load_framer(cfg) -> DocFramer:
    from chunkshop.config import (
        IdentityFramerConfig,
        HeadingBoundaryFramerConfig,
        RegexBoundaryFramerConfig,
    )

    if isinstance(cfg, IdentityFramerConfig):
        return IdentityFramer()
    if isinstance(cfg, HeadingBoundaryFramerConfig):
        return HeadingBoundaryFramer(cfg)
    if isinstance(cfg, RegexBoundaryFramerConfig):
        return RegexBoundaryFramer(cfg)
    raise ValueError(f"unknown framer type: {type(cfg).__name__}")


__all__ = [
    "DocFramer",
    "IdentityFramer",
    "HeadingBoundaryFramer",
    "RegexBoundaryFramer",
    "load_framer",
]
