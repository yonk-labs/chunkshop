"""Experimental rest_api connector — stub.

Registered for entry-point discoverability; not yet behaviourally
implemented. See `docs/connectors/_status.md` for the lift status
and `chunkshop_connectors/_stub.py` for the stub contract.
"""
from chunkshop_connectors._stub import make_stub

Connector, factory = make_stub("rest_api")

__all__ = ["Connector", "factory"]
