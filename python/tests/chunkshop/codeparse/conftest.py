"""Shared fixtures for codeparse tests.

``FIXTURES_DIR`` is the absolute path to ``python/tests/fixtures/codeparse``
so tests stay agnostic of where pytest is invoked from. Per CLAUDE.md
discipline: tests use absolute paths, not relative.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# python/tests/chunkshop/codeparse/conftest.py
#   └─ python/tests/chunkshop/codeparse/
#         parents[0] = python/tests/chunkshop/codeparse
#         parents[1] = python/tests/chunkshop
#         parents[2] = python/tests
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "codeparse"


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to the per-language fixture root."""
    return FIXTURES_DIR
