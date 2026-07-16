"""Focused exceptions control-flow analysis."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import (
    dataclass,
    field,
    fields,
    is_dataclass,
    replace,
)
from enum import Enum, auto
from hashlib import sha1
from itertools import count
from pathlib import Path
from typing import cast

import valiance.analysis.annotations as annotation_hooks
import valiance.vtypes as T
import valiance.analysis.where_clause as static_where
from valiance.elements.builtins import default_environment
from valiance.analysis.lints import (
    DEFAULT_REGISTRY as DEFAULT_LINT_REGISTRY,
    BlockLintContext,
    LintFinding,
    LintRegistry,
    MatchLintContext,
    NodeLintContext,
)
from valiance.asts import (
    AnnotationNode,
    ASTNode,
    BindingPatternNode,
    DefineNode,
    ElementExtension,
    ElementNode,
    EnumMemberNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    ImportComponent,
    ImportPath,
    ImportSpec,
    ListPatternNode,
    MatchCaseNode,
    MatchNode,
    MatchPatternNode,
    ObjectNode,
    OrPatternNode,
    PopNNode,
    SourceLocation,
    TraitRequirementNode,
    TryHandlerNode,
    TryNode,
    TypePatternNode,
    TypedCallNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedFunctionNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedMatchNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedTryNode,
    VariantMemberNode,
    is_default_match_case,
)
from valiance.asts.nodes import GetVariableNode, ObjectFieldNode
from valiance.modules_system.modules import ModuleLoader, ModuleLoadError, import_definitions
from valiance.asts.object_constructors import (
    constructor_definitions,
    definitely_initialized_fields,
    prepare_constructor_body,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.default_types import Boolean

from ..calls import candidates as _calls
from ..calls import callable_values as _functions
from . import patterns as _patterns
from .. import _analyser_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)

from ..calls.models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






class _ExceptionAnalysis:
    """Own exceptions control-flow operations."""

    def _try(
        self,
        node: TryNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Analyse a `TryNode` node and return the surviving branches."""
        if not node.handlers:
            self._diagnose("try requires at least one handler", node)
            return BranchSet()

        body_outputs = self.analyse_scoped_block(BranchSet((branch,)), node.body)
        typed_body = _patterns._typed_block(
            body_outputs,
            len(branch.typed_body),
            node.body,
        )
        outputs: list[AnalysisBranch] = list(body_outputs.branches)
        typed_handler_bodies: list[tuple[ASTNode | TypedNode, ...]] = []
        for handler in node.handlers:
            if handler.typ is not None:
                normalized_handler = T.normalize(handler.typ)
                if (
                    isinstance(normalized_handler, T.NominalType)
                    and self.env.lookup_trait(normalized_handler.name) is not None
                ):
                    self._diagnose(
                        f"try handler type {T.show(handler.typ)} is not a concrete "
                        "runtime fault type",
                        handler,
                    )
                elif not T.assignable(
                    handler.typ,
                    T.N(Symbol("Fault")),
                    self.env.context,
                ):
                    self._diagnose(
                        f"try handler type {T.show(handler.typ)} does not "
                        "implement Fault",
                        handler,
                    )
            handler_outputs = self.analyse_scoped_block(
                BranchSet((branch,)),
                handler.body,
            )
            typed_handler_bodies.append(
                _patterns._typed_block(
                    handler_outputs,
                    len(branch.typed_body),
                    handler.body,
                )
            )
            for output in handler_outputs:
                if output.inputs != branch.inputs:
                    self._diagnose("try handlers inferred different inputs", handler)
                    continue
                outputs.append(_patterns._try_handler_output(output, branch, handler))

        if not outputs:
            return BranchSet()

        joined: AnalysisBranch | None = None
        for output in outputs:
            joined = _patterns._join_try_output(
                branch,
                joined,
                output,
                self.env.context,
            )
            if joined is None:
                self._diagnose("try branches inferred different inputs", node)
                return BranchSet()
        if joined is None:
            return BranchSet()
        return BranchSet(
            (
                joined.emit(
                    TypedTryNode(
                        node,
                        _calls._returns_result_type(joined.stack.items),
                        body=typed_body,
                        handler_bodies=tuple(typed_handler_bodies),
                    )
                ),
            )
        )

