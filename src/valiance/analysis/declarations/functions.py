"""Focused functions declaration analysis."""

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
from .. import _analyser_patterns as _patterns
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


class _FunctionDeclarations:
    """Own declaration operations for this domain."""

    def _define(
        self,
        node: DefineNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Analyse a `DefineNode` node and return the surviving branches."""
        name = node.name
        function_node = node.function
        if not self._validate_annotations(node.annotations, "define", node):
            return BranchSet((branch.emit(TypedNode(node, None)),))
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            function_node,
            node.annotations,
        )
        if not function_node.overloads:
            function_node = _functions._genericize_function_node(
                function_node,
                node.generics,
            )
        function_node = replace(
            function_node,
            generics=node.generics,
            generic_variances=node.generic_variances,
            generic_constraints=node.generic_constraints,
        )
        self._validate_function_element_tags(function_node, node)
        declared_overload = (
            _functions._fully_typed_overload(function_node)
            if not node.generics
            and _functions._body_references_element(function_node.body, name)
            else None
        )
        if (
            declared_overload is not None
            and not self.env.has_local_non_object_friendly_overload(
                name,
                declared_overload,
            )
        ):
            self.env.define_overload(name, declared_overload)
        result = self._analyse_overloaded_function_literal(branch, function_node, node)
        if result is None:
            return BranchSet((branch.emit(TypedNode(node, None)),))
        function, typed_branch = result
        generic_constraints = _functions._generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        overload_typings = list(function.overloads)
        for typing_index, typing in enumerate(function.overloads):
            if not isinstance(typing.overload, T.Overload):
                continue
            overload = self.prepare_defined_overload(
                node,
                branch,
                typing.overload,
                generic_constraints,
            )
            if overload is None:
                continue
            overload_typings[typing_index] = replace(typing, overload=overload)
            if not self.env.has_local_non_object_friendly_overload(name, overload):
                self.env.define_overload(name, overload)
            original_index = self.env.non_object_friendly_overload_index(
                name,
                overload,
            )
            if name.text.startswith("#") and original_index is not None:
                static_result = _calls._static_validator_result(typing.body)
                if static_result is not None:
                    self.env.set_tag_validator_static_result(
                        name,
                        original_index,
                        static_result,
                    )
            if annotation_hooks.has_annotation(node.annotations, "commutative"):
                for generated in annotation_hooks.commutative_overloads(overload):
                    if not self.env.has_non_object_friendly_overload(
                        name,
                        generated,
                    ):
                        self.env.define_overload(name, generated)
                    overload_typings.append(
                        annotation_hooks.commutative_overload_typing(
                            name,
                            overload,
                            generated,
                            original_index or 0,
                        )
                    )
        if node.attached_tag is not None:
            if self.env.lookup_tag(node.attached_tag.name) is None:
                self._diagnose(
                    f"cannot attach element '{name}' to undeclared tag "
                    f"'#{node.attached_tag.name}'",
                    node,
                )
            self.env.define_tag_attached_element(node.attached_tag.name, name)
        typed_node = TypedFunctionNode(node, function.typ, tuple(overload_typings))
        return BranchSet((typed_branch.emit(typed_node),))

    def prepare_defined_overload(
        self,
        node: DefineNode,
        _branch: AnalysisBranch,
        overload: T.Overload,
        generic_constraints: tuple[T.GenericConstraint, ...],
    ) -> T.Overload | None:
        """Apply definition annotations and register the resulting overload metadata."""
        name = node.name
        if not _functions._validate_define_niladic_name(name, overload):
            if name.text.startswith("\\"):
                self._diagnose(
                    f"{name} named as nilad, but inferred as popping "
                    f"{len(overload.params)} value(s)",
                    node,
                )
            else:
                self._diagnose(
                    f"{name} inferred as nilad, but not named as one",
                    node,
                )
            return None

        if name.text.startswith("#") and not _calls._validator_overload_ok(
            overload,
            self.env.context,
        ):
            self._diagnose(
                f"tag validator '{name}' must return #boolean Number",
                node,
            )
            return None

        if not self._validate_data_tags((overload.params, overload.returns), node):
            return None
        overload = _functions._with_generic_constraints(overload, generic_constraints)
        overload = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
            overload,
            node.annotations,
        )

        if not node.is_multi:
            return overload

        overload = replace(overload, is_multi=True)
        if _functions._has_multimethod_fallback(
            overload,
            self.env.overloads_for(name),
            self.env.context,
        ):
            return overload

        self._diagnose(
            f"multi define '{name}' requires a non-multi fallback "
            "with compatible parameters and identical returns",
            node,
        )
        return None

