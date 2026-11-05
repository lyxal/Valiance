"""Focused validation contract validation."""

from __future__ import annotations


import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.asts import ASTNode
from valiance.vtypes.default_types import Boolean


class _AnnotationContracts:
    """Own validation contract operations."""

    def _validate_annotations(
        self,
        annotations: tuple[ASTNode, ...],
        target: str,
        node: ASTNode,
    ) -> bool:
        """Return the Boolean result of validate annotations during static analysis."""
        diagnostics = annotation_hooks.DEFAULT_REGISTRY.validate(
            annotations,
            target,
            node,
        )
        for diagnostic in diagnostics:
            self._diagnose(diagnostic, node)
        return not diagnostics

