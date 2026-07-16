"""Focused objects declaration analysis."""

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

from .. import _analyser_calls as _calls
from .. import _analyser_functions as _functions
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

class _ObjectDeclarations:
    """Own objects declaration operations."""

    def _object_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build the definition for object during static analysis."""
        if not self._validate_object_lifecycle(node):
            return BranchSet((branch.emit(TypedNode(node, None)),))
        if node.target is not None:
            if node.fields:
                self._diagnose(
                    "trait implementation blocks cannot declare fields",
                    node,
                )
                return BranchSet((branch.emit(TypedNode(node, None)),))
            target = T.normalize(node.target)
            if isinstance(target, T.NominalType):
                self.env.add_trait_impl(node.name, target.name)
            current = self._register_friendly_definitions(
                branch.emit(TypedNode(node, None)),
                node.name,
                node.definitions,
            )
            return BranchSet((current,))

        object_attributes = self._object_attributes(node.fields, node.generics)
        if object_attributes is None:
            return BranchSet((branch.emit(TypedNode(node, None)),))
        defaults = frozenset(field.name for field in node.fields if field.default)
        constructors = constructor_definitions(node.name, node.definitions)
        friendly_definitions = tuple(
            definition
            for definition in node.definitions
            if definition not in constructors
        )
        self._define_object_shape(
            node.name,
            node,
            object_attributes,
            defaults=defaults,
            synthesize_constructor=not constructors,
        )
        current = branch.emit(TypedNode(node, None))
        for constructor in constructors:
            current = self._register_constructor_definition(
                current,
                node,
                constructor,
                defaults,
            )
        current = self._register_friendly_definitions(
            current,
            node.name,
            friendly_definitions,
        )
        return BranchSet((current,))

    def _object_attribute(self, field: ObjectFieldNode) -> T.ObjectAttribute | None:
        """Compute object attribute during static analysis."""
        if field.typ is not None:
            typ = field.typ
        elif field.default:
            diagnostics_before = len(self.diagnostics)
            outputs = self.analyse_scoped_block(
                BranchSet((AnalysisBranch(input_mode=InputMode.TOP_LEVEL),)),
                field.default,
            )
            types = tuple(output.stack[-1] for output in outputs if output.stack)
            if not types:
                if outputs or len(self.diagnostics) == diagnostics_before:
                    self._diagnose(
                        f"default for field '{field.name}' must leave a value",
                        field,
                    )
                return None
            typ = T.U(*types)
        else:
            self._diagnose(f"field '{field.name}' needs a type", field)
            return None
        return T.ObjectAttribute(
            field.name,
            typ,
            field.access,
            has_default=bool(field.default),
        )

    def _object_attributes(
        self,
        fields: tuple[ObjectFieldNode, ...],
        generics: tuple[Symbol, ...],
    ) -> tuple[T.ObjectAttribute, ...] | None:
        """Compute object attributes during static analysis."""
        attributes = tuple(self._object_attribute(field) for field in fields)
        if any(attribute is None for attribute in attributes):
            return None
        return tuple(
            _functions._genericize_attribute(attribute, generics)
            for attribute in attributes
            if attribute is not None
        )

    def _define_object_shape(
        self,
        name: Symbol,
        node: ObjectNode,
        attributes: tuple[T.ObjectAttribute, ...],
        *,
        defaults: frozenset[Symbol] = frozenset(),
        result_type: T.Type | None = None,
        generic_constraints: tuple[T.GenericConstraint, ...] | None = None,
        synthesize_constructor: bool = True,
    ) -> None:
        """Record object shape during static analysis."""
        constraints = (
            _functions._generic_constraints(
                node.generics,
                node.generic_variances,
                node.generic_constraints,
            )
            if generic_constraints is None
            else generic_constraints
        )
        self.env.define_object(
            name,
            attributes,
            generics=node.generics,
            generic_variance=_functions._declared_or_inferred_variance(
                node.generics,
                node.generic_variances,
                attributes,
                (),
            ),
        )
        if annotation_hooks.has_annotation(node.annotations, "errType"):
            self.env.add_trait_impl(name, Symbol("Err"))
        if synthesize_constructor:
            self.env.define_constructor(
                name,
                attributes,
                defaults=defaults,
                result_type=result_type
                or _utils._declared_nominal(name, node.generics),
                generic_constraints=constraints,
            )
        else:
            self.env.define_constructor_metadata(
                name,
                attributes,
                defaults=defaults,
                generic_constraints=constraints,
            )

    def _register_constructor_definition(
        self,
        branch: AnalysisBranch,
        owner_node: ObjectNode,
        definition: DefineNode,
        defaults: frozenset[Symbol],
    ) -> AnalysisBranch:
        """Register constructor definition during static analysis."""
        if not self._validate_annotations(definition.annotations, "define", definition):
            return branch

        owner = owner_node.name
        owner_definition = self.env.lookup_object(owner)
        self_type = _utils._declared_nominal(
            owner,
            owner_definition.generics if owner_definition is not None else (),
        )
        if definition.function.returns is not None and (
            len(definition.function.returns) != 1
            or not T.same(
                _functions._genericize_type(
                    definition.function.returns[0],
                    (*owner_node.generics, *definition.generics),
                ),
                self_type,
            )
        ):
            self._diagnose(
                f"constructor '{owner}' must return {T.show(self_type)}",
                definition,
            )
            return branch

        body = prepare_constructor_body(definition.function.body)
        initialized = definitely_initialized_fields(body, defaults)
        missing = tuple(
            field.name for field in owner_node.fields if field.name not in initialized
        )
        if missing:
            self._diagnose(
                f"constructor '{owner}' does not initialize field(s): "
                + ", ".join(str(name) for name in missing),
                definition,
            )
            return branch

        function_node = FunctionNode(
            params=definition.function.params,
            body=(*body, GetVariableNode(Symbol("self"), location=definition.location)),
            returns=(self_type,),
            where_clause=definition.function.where_clause,
            element_tags=definition.function.element_tags,
            annotations=definition.function.annotations,
            element_tags_explicit=definition.function.element_tags_explicit,
            companion_tags_allowed=definition.function.companion_tags_allowed,
            location=definition.function.location,
        )
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            function_node,
            definition.annotations,
        )
        function_node = _functions._genericize_function_node(
            function_node,
            (*owner_node.generics, *definition.generics),
        )
        function_node = replace(
            function_node,
            generics=(*owner_node.generics, *definition.generics),
            generic_variances=(
                *owner_node.generic_variances,
                *definition.generic_variances,
            ),
            generic_constraints=(
                *owner_node.generic_constraints,
                *definition.generic_constraints,
            ),
        )
        self._validate_function_element_tags(function_node, definition)
        self._friendly_owners = self._friendly_owners + (owner,)
        try:
            result = self._analyse_function_literal(
                branch,
                function_node,
                initial_function_locals=((Symbol("self"), self_type),),
            )
        finally:
            self._friendly_owners = self._friendly_owners[:-1]
        if result is None:
            return branch

        function, typed_branch = result
        generic_constraints = (
            *_functions._generic_constraints(
                owner_node.generics,
                owner_node.generic_variances,
                owner_node.generic_constraints,
            ),
            *_functions._generic_constraints(
                definition.generics,
                definition.generic_variances,
                definition.generic_constraints,
            ),
        )
        for typing in function.overloads:
            if not isinstance(typing.overload, T.Overload):
                continue
            overload = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
                _functions._with_generic_constraints(
                    typing.overload,
                    generic_constraints,
                ),
                definition.annotations,
            )
            if overload not in self.env.overloads_for(owner):
                existing = self.env.overloads_for(owner)
                if existing and len(overload.params) != len(existing[0].params):
                    self._diagnose(
                        f"constructor overloads for '{owner}' must all take "
                        f"{len(existing[0].params)} inputs, got "
                        f"{len(overload.params)}",
                        definition,
                    )
                    continue
                self.env.define_overload(owner, overload)
        return typed_branch

    def _register_friendly_definition(
        self,
        branch: AnalysisBranch,
        owner: Symbol,
        definition: DefineNode,
    ) -> AnalysisBranch:
        """Register friendly definition during static analysis."""
        if not self._validate_annotations(definition.annotations, "define", definition):
            return branch.emit(TypedNode(definition, None))
        owner_definition = self.env.lookup_object(owner)
        self_type = _utils._declared_nominal(
            owner,
            owner_definition.generics if owner_definition is not None else (),
        )
        params = (FunctionParam(Symbol("self"), self_type),) + tuple(
            definition.function.params or ()
        )
        body = definition.function.body
        if annotation_hooks.has_annotation(definition.annotations, "self"):
            body = prepare_constructor_body(body)
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            FunctionNode(
                params=params,
                body=body,
                returns=definition.function.returns,
                where_clause=definition.function.where_clause,
                element_tags=definition.function.element_tags,
                annotations=definition.function.annotations,
                location=definition.function.location,
            ),
            definition.annotations,
        )
        function_node = replace(
            function_node,
            params=(replace(function_node.params[0], name=None),)
            + function_node.params[1:],
        )
        function_node = _functions._genericize_function_node(
            function_node,
            definition.generics,
        )
        self._friendly_owners = self._friendly_owners + (owner,)
        try:
            result = self._analyse_function_literal(
                branch,
                function_node,
                initial_function_locals=((Symbol("self"), self_type),),
            )
        finally:
            self._friendly_owners = self._friendly_owners[:-1]
        if result is None:
            return branch.emit(TypedNode(definition, None))
        function, typed_branch = result
        generic_constraints = _functions._generic_constraints(
            definition.generics,
            definition.generic_variances,
            definition.generic_constraints,
        )
        for name in (definition.name, Symbol(f"{owner}::{definition.name}")):
            object_friendly = name == definition.name
            for typing in function.overloads:
                if not isinstance(typing.overload, T.Overload):
                    continue
                self.env.define_overload(
                    name,
                    annotation_hooks.DEFAULT_REGISTRY.transform_overload(
                        _functions._with_generic_constraints(
                    typing.overload,
                    generic_constraints,
                ),
                        definition.annotations,
                    ),
                    object_friendly=object_friendly,
                )
        return typed_branch

    def _register_friendly_definitions(
        self,
        branch: AnalysisBranch,
        owner: Symbol,
        definitions: tuple[DefineNode, ...],
    ) -> AnalysisBranch:
        """Register friendly definitions during static analysis."""
        current = branch
        for definition in definitions:
            current = self._register_friendly_definition(current, owner, definition)
        return current

