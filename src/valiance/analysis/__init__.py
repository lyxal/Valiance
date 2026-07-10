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
from valiance.analysis.lints import (
    DEFAULT_REGISTRY,
    BlockLintContext,
    LintFinding,
    LintRegistry,
    LintRewrite,
    MatchLintContext,
    NodeLintContext,
    RewriteKind,
    finding,
)

__all__ = [
    "AnalysisBranch",
    "BlockLintContext",
    "DEFAULT_REGISTRY",
    "Analyser",
    "BranchSet",
    "BranchVariables",
    "FunctionAnalysis",
    "InputMode",
    "LintFinding",
    "LintRegistry",
    "LintRewrite",
    "MatchLintContext",
    "NodeLintContext",
    "RewriteKind",
    "analyse",
    "analyse_function",
    "analyse_function_details",
    "default_environment",
    "finding",
]
