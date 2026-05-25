"""Experimental-tier stub factory.

Many of the connectors lifted from RAGFlow exist as *names* in chunkshop's
registry before their full behavioural surface is ported. We register
them as `@experimental` stubs so::

    >>> from chunkshop.sources import registry
    >>> "notion" in registry.available_connectors()
    True

…holds today, and consumers get a clear, named `StubError` if they
actually try to iterate documents — not a `KeyError` or a vague
"unknown connector".

Real implementations replace `make_stub("notion")` in
``chunkshop_connectors/notion/__init__.py`` with the lifted connector
class. Until then, the surface is intentionally narrow: import works,
registry sees it, instantiation succeeds, iteration raises.
"""
from __future__ import annotations

from typing import Any, Callable

from chunkshop.sources.base import SyncMode

from chunkshop_connectors._tier import experimental


class StubError(NotImplementedError):
    """Raised when an experimental stub connector is iterated.

    The connector is *registered* (importable, in the registry, type
    `experimental` per `tier_of`) but its real implementation hasn't
    landed yet. See ``docs/connectors/_status.md`` for the roadmap.
    """


def make_stub(name: str) -> tuple[type, Callable[[dict[str, Any]], Any]]:
    """Build a ``(Connector, factory)`` pair for an experimental stub.

    The returned class is decorated `@experimental` so `tier_of(cls)`
    yields `"experimental"`. The factory accepts any config dict (no
    validation — experimental connectors have no contract yet).
    """

    @experimental
    class StubConnector:
        sync_mode = SyncMode.FULL_RESYNC

        def __init__(self, config: dict[str, Any]):
            self.config = config
            self._name = name

        def iter_documents(self):
            raise StubError(
                f"connector {self._name!r} is registered as experimental "
                f"but not yet implemented. See docs/connectors/_status.md."
            )

    StubConnector.__name__ = f"{name.capitalize()}StubConnector"
    StubConnector.__qualname__ = StubConnector.__name__

    def factory(config: dict[str, Any]) -> StubConnector:
        return StubConnector(config)

    return StubConnector, factory
