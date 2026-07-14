"""Valiance test discovery, selection, and execution support."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = frozenset(
    {
        "TestCase",
        "TestCommandError",
        "TestGroup",
        "TestResult",
        "discover_tests",
        "run_test_command",
    }
)


def __getattr__(name: str):
    """Load the test runner only when a public testing symbol is requested."""
    if name not in _EXPORTS:
        raise AttributeError(name)
    return getattr(import_module("valiance.testing.runner"), name)


__all__ = sorted(_EXPORTS)
