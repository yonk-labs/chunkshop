"""Connector tier markers.

Every connector in this package belongs to one of two tiers:

- **verified** — behaviourally tested against hermetic per-provider
  mocks; cursor / prune semantics exercised; ready for production use.
- **experimental** — imports and registers; full behaviour not yet
  certified. Use at your own risk.

Decorate the connector class with `@verified` or `@experimental`. The
tier is readable at runtime via `tier_of(Connector)`. Connectors with
no tier marker default to `"experimental"` so accidental omission
errs on the side of safety.
"""
from __future__ import annotations


def verified(cls):
    """Mark a connector class as belonging to the verified tier."""
    cls.__connector_tier__ = "verified"
    return cls


def experimental(cls):
    """Mark a connector class as belonging to the experimental tier."""
    cls.__connector_tier__ = "experimental"
    return cls


def tier_of(cls) -> str:
    """Return the tier of a connector class. Defaults to "experimental"."""
    return getattr(cls, "__connector_tier__", "experimental")
