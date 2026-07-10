"""Public entry points for static analysis and the default type environment."""

from valiance.analysis.analyser import (
    Analyser,
    AnalysisBranch,
    BranchSet,
    BranchVariables,
    FunctionAnalysis,
    InputMode,
    analyse,
    analyse_function,
    analyse_function_details,
)
from valiance.analysis.builtins import default_environment
from valiance.analysis.lints import LintFinding, LintRewrite, RewriteKind

__all__ = [
    "AnalysisBranch",
    "Analyser",
    "BranchSet",
    "BranchVariables",
    "FunctionAnalysis",
    "InputMode",
    "LintFinding",
    "LintRewrite",
    "RewriteKind",
    "analyse",
    "analyse_function",
    "analyse_function_details",
    "default_environment",
]
