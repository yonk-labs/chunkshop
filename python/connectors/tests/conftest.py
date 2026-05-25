"""Shared pytest fixtures for the chunkshop-connectors test suite.

Re-exports per-connector mock fixtures from
``chunkshop_connectors.testing.mocks.*`` so individual test files
don't have to know which module defines each fixture.
"""
from chunkshop_connectors.testing.mocks.blob import blob_mock  # noqa: F401
