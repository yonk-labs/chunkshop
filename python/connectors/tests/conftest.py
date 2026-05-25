"""Shared pytest fixtures for the chunkshop-connectors test suite.

Re-exports per-connector mock fixtures from
``chunkshop_connectors.testing.mocks.*`` so individual test files
don't have to know which module defines each fixture.

Also installs a session-scoped guard that raises if any test tries
to open a TCP connection to a non-loopback address. Hermetic mocks
must be the only HTTP endpoints touched — anything else is a bug.
"""
from __future__ import annotations

import socket

import pytest

from chunkshop_connectors.testing.mocks.blob import blob_mock  # noqa: F401
from chunkshop_connectors.testing.mocks.gdrive import gdrive_mock  # noqa: F401
from chunkshop_connectors.testing.mocks.github import github_mock  # noqa: F401
from chunkshop_connectors.testing.mocks.notion import notion_mock  # noqa: F401
from chunkshop_connectors.testing.mocks.rss import rss_mock  # noqa: F401


_LOOPBACK_PREFIXES = ("127.", "::1", "localhost")


@pytest.fixture(autouse=True)
def _block_non_loopback_sockets(monkeypatch):
    """Forbid TCP connections to non-loopback addresses during tests.

    Allows ``127.x``, ``::1``, and ``localhost`` so the
    ``pytest_httpserver`` fixture and any UNIX-socket-on-loopback
    machinery keep working. Anything else (e.g. ``api.github.com``,
    ``s3.amazonaws.com``) raises immediately — tests must use the
    hermetic mocks.
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def _guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if not isinstance(host, str) or not host.startswith(_LOOPBACK_PREFIXES):
            raise RuntimeError(
                f"connectors tests must not open sockets to non-loopback hosts "
                f"(attempted: {host!r})"
            )
        return real_connect(self, address)

    def _guarded_getaddrinfo(host, *args, **kwargs):
        # Permit DNS resolution for loopback only; many libs resolve
        # before connecting.
        if isinstance(host, str) and host.startswith(_LOOPBACK_PREFIXES):
            return real_getaddrinfo(host, *args, **kwargs)
        # Allow None / numeric hosts through (used by getaddrinfo for
        # AI_PASSIVE listen sockets — pytest_httpserver does this).
        if host is None:
            return real_getaddrinfo(host, *args, **kwargs)
        raise RuntimeError(
            f"connectors tests must not resolve non-loopback hosts "
            f"(attempted: {host!r})"
        )

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    yield
