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
from ..control_flow import patterns as _patterns
from .. import _analyser_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
def _prelude_seed(source_file: Path | None) -> str:
    """Return a stable internal namespace seed for imported declarations."""
    identity = "<inline>" if source_file is None else str(source_file.resolve())
    return sha1(identity.encode("utf-8")).hexdigest()[:12]


def _with_import_runtime_name(
    node: TypedNode,
    runtime_name: Symbol,
) -> TypedNode:
    """Attach a hidden runtime binding without changing source-level names."""
    if isinstance(node, TypedFunctionNode):
        return TypedImportedFunctionNode(
            node.node,
            node.typ,
            node.overloads,
            node.dispatch_plan,
            runtime_name,
        )
    if isinstance(node.node, ObjectNode):
        return TypedImportedObjectNode(node.node, node.typ, runtime_name)
    return node


def _nested_types(typ: T.Type) -> Iterator[T.Type]:
    """Yield a normalized type and every nested type it contains."""
    typ = T.normalize(typ)
    yield typ
    if isinstance(typ, T.TaggedType):
        yield from _nested_types(typ.inner)
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            yield from _nested_types(arg)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            yield from _nested_types(item)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            yield from _nested_types(item)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            yield from _nested_types(item.typ)
        return
    if isinstance(typ, T.RowType):
        yield from _nested_types(typ.base)
        for field in typ.fields:
            yield from _nested_types(field.typ)
        return
    if isinstance(typ, T.CollectionType):
        yield from _nested_types(typ.base)
        return
    if isinstance(typ, T.FunctionType):
        for item in (*(typ.params or ()), *(typ.returns or ())):
            yield from _nested_types(item)
        for element_tag in typ.element_tags:
            for arg in element_tag.args:
                yield from _nested_types(arg)
        return
    if isinstance(typ, (T.ExactType, T.AtomicType)):
        yield from _nested_types(typ.inner)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for item in (
                *requirement.overload.params,
                *requirement.overload.returns,
            ):
                yield from _nested_types(item)
        return
    if isinstance(typ, T.OverloadSetType):
        for overload in typ.overloads:
            for item in (*overload.params, *overload.returns):
                yield from _nested_types(item)


def _all_data_tags(typ: T.Type) -> Iterator[T.DataTag]:
    """Yield every data-tag requirement nested inside one type."""
    for nested in _nested_types(typ):
        if isinstance(nested, T.TaggedType):
            yield from nested.tags


def _match_pattern_types(pattern: MatchPatternNode) -> Iterator[T.Type]:
    """Yield every explicit runtime type nested inside a match pattern."""
    if isinstance(pattern, TypePatternNode):
        if pattern.typ is not None:
            yield pattern.typ
        for field in pattern.fields:
            yield from _match_pattern_types(field)
        return
    if isinstance(pattern, BindingPatternNode):
        yield from _match_pattern_types(pattern.pattern)
        return
    if isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            yield from _match_pattern_types(option)
        return
    if isinstance(pattern, ListPatternNode):
        for item in pattern.items:
            yield from _match_pattern_types(item)


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
            ),
            requirements=all_requirements,
        )
        if parent_name is not None:
            self.env.add_trait_parent(name, parent_name)

