# src/chunkshop/sources/registry.py
"""Entry-point discovery for connector sources.

Connectors register against the ``chunkshop.sources`` entry-point group:

    [project.entry-points."chunkshop.sources"]
    gdrive = "chunkshop_connectors.gdrive:factory"

A factory is a callable ``(config: dict) -> Source``. Discovery is lazy and
cached; adding a connector requires NO edit to chunkshop core.
"""
from __future__ import annotations

import warnings
from importlib.metadata import entry_points
from typing import Callable

ENTRY_POINT_GROUP = "chunkshop.sources"

_cache: dict[str, Callable] | None = None


class UnknownConnectorError(KeyError):
    """Requested connector name is not registered by any installed plugin."""


def _iter_entry_points():
    return list(entry_points(group=ENTRY_POINT_GROUP))


def _registry() -> dict[str, Callable]:
    """Load all registered connector factories, per-plugin isolated.

    If one plugin's entry point fails to load (broken transitive dep, syntax
    error, ImportError), it must NOT prevent other healthy plugins from
    resolving. We warn and skip the broken one so the rest of the registry
    remains usable.
    """
    global _cache
    if _cache is None:
        loaded: dict[str, Callable] = {}
        for ep in _iter_entry_points():
            try:
                loaded[ep.name] = ep.load()
            except Exception as exc:  # noqa: BLE001 -- intentional: any plugin failure is isolated
                warnings.warn(
                    f"failed to load chunkshop.sources entry point {ep.name!r} "
                    f"({ep.value}): {type(exc).__name__}: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )
        _cache = loaded
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
