# src/chunkshop/testing/fixtures.py
"""pytest fixtures for connector testing. Consumers add
`pytest_plugins = ["chunkshop.testing.fixtures"]` to their conftest."""
import pytest
from chunkshop.oauth import MockOAuthProvider


@pytest.fixture
def mock_oauth_provider():
    return MockOAuthProvider()
