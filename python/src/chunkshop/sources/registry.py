# src/chunkshop/sources/registry.py
"""Entry-point discovery for connector sources.

Connectors register against the ``chunkshop.sources`` entry-point group:

    [project.entry-points."chunkshop.sources"]
    gdrive = "chunkshop_connectors.gdrive:factory"

A factory is a callable ``(config: dict) -> Source``. Discovery is lazy and
cached; adding a connector requires NO edit to chunkshop core.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Callable

ENTRY_POINT_GROUP = "chunkshop.sources"

_cache: dict[str, Callable] | None = None


class UnknownConnectorError(KeyError):
    """Requested connector name is not registered by any installed plugin."""


def _iter_entry_points():
    return list(entry_points(group=ENTRY_POINT_GROUP))


def _registry() -> dict[str, Callable]:
    global _cache
    if _cache is None:
        _cache = {ep.name: ep.load() for ep in _iter_entry_points()}
    return _cache


def clear_cache() -> None:
    global _cache
    _cache = None


def available_connectors() -> list[str]:
    return sorted(_registry().keys())


def load_connector(name: str, config: dict):
    reg = _registry()
    try:
        factory = reg[name]
    except KeyError:
        installed = ", ".join(available_connectors()) or "(none installed)"
        raise UnknownConnectorError(
            f"unknown connector {name!r}; install a plugin that registers it. "
            f"Installed connectors: {installed}. "
            f"See docs/cookbook/authoring-connectors.md."
        ) from None
    return factory(config)
