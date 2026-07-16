"""Declared and inferred language-contract validation."""

from __future__ import annotations

from importlib import import_module


def __getattr__(name: str):
    """Load the contract coordinator without affecting standalone helpers."""
    if name == "ContractAnalyser":
        return getattr(import_module("valiance.analysis.contracts.service"), name)
    raise AttributeError(name)


__all__ = ["ContractAnalyser"]
