"""Imports + module constants + functions — exercises module_block grouping."""
import os
import sys
from pathlib import Path

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 5
ROOT_DIR = Path(__file__).parent


def get_timeout():
    """Return the configured timeout."""
    return DEFAULT_TIMEOUT


def get_root():
    """Return the configured root path."""
    return ROOT_DIR
