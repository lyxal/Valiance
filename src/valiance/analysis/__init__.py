"""Lazy public API for static analysis, lints, and the default environment."""

from __future__ import annotations

from importlib import import_module

_EXPORT_MODULES = {
    "Analyser": "valiance.analysis.analyser",
    "AnalysisBranch": "valiance.analysis.analyser",
    "BranchSet": "valiance.analysis.analyser",
    "BranchVariables": "valiance.analysis.analyser",
    "FunctionAnalysis": "valiance.analysis.analyser",
    "InputMode": "valiance.analysis.analyser",
    "analyse": "valiance.analysis.analyser",
    "analyse_function": "valiance.analysis.analyser",
    "analyse_function_details": "valiance.analysis.analyser",
    "default_environment": "valiance.elements.builtins",
    "DEFAULT_REGISTRY": "valiance.analysis.lints",
    "BlockLintContext": "valiance.analysis.lints",
    "LintFinding": "valiance.analysis.lints",
    "LintRegistry": "valiance.analysis.lints",
    "LintRewrite": "valiance.analysis.lints",
    "MatchLintContext": "valiance.analysis.lints",
    "NodeLintContext": "valiance.analysis.lints",
    "RewriteKind": "valiance.analysis.lints",
    "finding": "valiance.analysis.lints",
}


def __getattr__(name: str):
    """Import one public analysis symbol only when requested."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)


__all__ = sorted(_EXPORT_MODULES)
