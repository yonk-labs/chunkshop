"""Realistic fixture: nesting, a decorator, a method calling a free function."""
from __future__ import annotations

import functools


def load(raw: str) -> int:
    return int(raw)


@functools.lru_cache(maxsize=8)
def cached_double(n: int) -> int:
    return n * 2


class Pipeline:
    def run(self, raw: str) -> int:
        def step(v: int) -> int:
            return cached_double(v)

        value = load(raw)
        return step(value)
