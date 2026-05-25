"""Hermetic per-connector mocks.

Each submodule exposes a `pytest` fixture and a `valid_config` dict
suitable for instantiating its connector under test without touching
the network. See :mod:`chunkshop_connectors.testing.mocks.blob` and
:mod:`chunkshop_connectors.testing.mocks.rss`.
"""
