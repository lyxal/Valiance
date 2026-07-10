"""Automatic discovery for built-in lint rule modules."""

from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules

from ..registry import LintRegistry


def load_rules(registry: LintRegistry) -> None:
    """Import each rule module and ask it to register with ``registry``."""
    prefix = f"{__name__}."
    modules = sorted(iter_modules(__path__), key=lambda item: item.name)
    for module_info in modules:
        if module_info.name.startswith("_"):
            continue
        module = import_module(prefix + module_info.name)
        register = getattr(module, "register", None)
        if register is not None:
            register(registry)
