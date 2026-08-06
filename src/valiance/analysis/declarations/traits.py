"""Focused traits declaration analysis."""

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

import valiance.analysis.contracts.annotations as annotation_hooks
import valiance.vtypes as T
import valiance.analysis.contracts.where_clauses as static_where
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
    is_catch_all_match_case,
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
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

class _TraitDeclarations:
    """Own traits declaration operations."""

    def _trait_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build a trait and type-check its default methods against requirements."""
        self._define_trait_shape(node.name, node)
        trait = self.env.lookup_trait(node.name)
        self_type = _utils._declared_nominal(node.name, node.generics)

        # Trait requirements are abstract, so expose receiver-specialized versions
        # only while checking default bodies. Keeping them out of the persistent
        # overload table ensures concrete implementations retain stable runtime
        # overload indexes.
        snapshots: dict[Symbol, tuple[list[T.Overload] | None, set[int] | None]] = {}
        for requirement in trait.requirements if trait is not None else ():
            name = requirement.name
            if name not in snapshots:
                snapshots[name] = (
                    list(self.env.overloads[name])
                    if name in self.env.overloads
                    else None,
                    set(self.env.object_friendly_overloads[name])
                    if name in self.env.object_friendly_overloads
                    else None,
                )
            # Object-friendly elements receive their explicit arguments below
            # the receiver on the stack.  A default such as ``$self log``
            # therefore sees the requirement's arguments before ``self``.
            overload = replace(
                requirement.overload,
                params=(*requirement.overload.params, self_type),
                param_names=(*requirement.overload.param_names, None),
            )
            candidates = self.env.overloads.setdefault(name, [])
            index = len(candidates)
            candidates.append(overload)
            self.env.object_friendly_overloads.setdefault(name, set()).add(index)

        try:
            current = self._register_friendly_definitions(
                branch.emit(TypedNode(node, None)),
                node.name,
                node.definitions,
            )
        finally:
            for name, (overloads, friendly) in snapshots.items():
                if overloads is None:
                    self.env.overloads.pop(name, None)
                else:
                    self.env.overloads[name] = overloads
                if friendly is None:
                    self.env.object_friendly_overloads.pop(name, None)
                else:
                    self.env.object_friendly_overloads[name] = friendly
        return BranchSet((current,))

    def _define_trait_shape(self, name: Symbol, node: ObjectNode) -> None:
        """Record trait shape, including requirements inherited from a parent."""
        requirements = list(_utils._trait_requirements(node))
        parent_name: Symbol | None = None
        if node.target is not None:
            target = T.normalize(node.target)
            if isinstance(target, T.NominalType):
                parent_name = target.name
                parent = self.env.lookup_trait(parent_name)
                if parent is not None:
                    for requirement in parent.requirements:
                        if requirement not in requirements:
                            requirements.append(requirement)

        all_requirements = tuple(requirements)
        self.env.define_trait(
            name,
            generics=node.generics,
            generic_variance=_functions._declared_or_inferred_variance(
                node.generics,
                node.generic_variances,
                (),
                all_requirements,
                self.env.context,
            ),
            requirements=all_requirements,
        )
        if parent_name is not None:
            self.env.add_trait_parent(name, parent_name)

