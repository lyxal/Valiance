"""Dependency-aware incremental compilation services."""

from .database import AnalysisSnapshot, CompilationDatabase, ExecutableSnapshot

from .coordinator import BuildDisposition, BuildResult, CompilationCoordinator
from .inspection import (
    CacheIssue,
    CacheReport,
    ReasonCode,
    RebuildReason,
    clean_cache,
    inspect_cache,
    render_cache_report,
)

from .graph import ModuleComponent, discover_module_graph, strongly_connected_components
from .store import ArtifactStore, ArtifactStoreError
from .semantic import (
    DeclarationIdentity,
    DeclarationKind,
    DependencyKind,
    InvalidationResult,
    PublishedFact,
    SemanticDependency,
    SemanticProduct,
    SemanticProductStore,
    canonical_fingerprint,
    invalidate_products,
    semantic_input_fingerprint,
)

__all__ = (
    "AnalysisSnapshot",
    "CompilationDatabase",
    "ExecutableSnapshot",
    "ArtifactStore",
    "ArtifactStoreError",
    "CacheIssue",
    "CacheReport",
    "ReasonCode",
    "RebuildReason",
    "clean_cache",
    "inspect_cache",
    "render_cache_report",
    "BuildDisposition",
    "BuildResult",
    "CompilationCoordinator",
    "DeclarationIdentity",
    "DeclarationKind",
    "DependencyKind",
    "InvalidationResult",
    "PublishedFact",
    "SemanticDependency",
    "SemanticProduct",
    "SemanticProductStore",
    "canonical_fingerprint",
    "invalidate_products",
    "semantic_input_fingerprint",
    "ModuleComponent",
    "discover_module_graph",
    "strongly_connected_components",
)
