"""Sample Python module for codeparse tests."""
from __future__ import annotations


def helper(value: int) -> int:
    """Top-level free function."""
    return value * 2


class Calculator:
    """A class with two methods."""

    def add(self, a: int, b: int) -> int:
        """Sum two integers and double the result via helper()."""
        total = a + b
        return helper(total)

    def multiply(self, a: int, b: int) -> int:
        """Multiply two integers."""
        return a * b
