"""Public API for Valiance source-maintenance and source-emission tools."""

from __future__ import annotations

from importlib import import_module

_TOOL_EXPORTS = frozenset(
    {
        "DEFAULT_REFERENCE_FILENAME",
        "DefinitionReference",
        "DocField",
        "ParsedDocstring",
        "add_missing_docstrings",
        "extract_documented_defines",
        "format_source",
        "parse_docstring",
        "project_source_files",
        "render_html_reference",
    }
)


def __getattr__(name: str):
    """Load the heavier source-tool implementation only when first requested."""
    if name not in _TOOL_EXPORTS:
        raise AttributeError(name)
    return getattr(import_module("valiance.source_tools.tools"), name)


__all__ = sorted(_TOOL_EXPORTS)
