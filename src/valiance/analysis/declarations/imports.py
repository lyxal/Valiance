"""Focused imports declaration analysis."""

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
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
def _prelude_seed(source_file: Path | None) -> str:
    """Return a stable internal namespace seed for imported declarations."""
    identity = "<inline>" if source_file is None else str(source_file.resolve())
    return sha1(identity.encode("utf-8")).hexdigest()[:12]


def _rewrite_imported_self_calls(
    value,
    source_name: Symbol,
    runtime_name: Symbol,
):
    """Retarget recursive calls inside an imported definition to its hidden binding."""
    if isinstance(value, TypedElementNode):
        rewritten = {
            field.name: _rewrite_imported_self_calls(
                getattr(value, field.name), source_name, runtime_name
            )
            for field in fields(value)
        }
        if isinstance(value.node, ElementNode) and value.node.name == source_name:
            rewritten["runtime_name"] = runtime_name
            rewritten["overload_index"] = 0
        return replace(value, **rewritten)
    if isinstance(value, tuple):
        return tuple(
            _rewrite_imported_self_calls(item, source_name, runtime_name)
            for item in value
        )
    if is_dataclass(value) and not isinstance(value, ASTNode):
        rewritten = {
            field.name: _rewrite_imported_self_calls(
                getattr(value, field.name), source_name, runtime_name
            )
            for field in fields(value)
        }
        return replace(value, **rewritten)
    return value


def _with_import_runtime_name(
    node: TypedNode,
    runtime_name: Symbol,
) -> TypedNode:
    """Attach a hidden runtime binding without changing source-level names."""
    if isinstance(node, TypedFunctionNode):
        source_name = (
            node.node.name if isinstance(node.node, DefineNode) else Symbol("")
        )
        overloads = _rewrite_imported_self_calls(
            node.overloads, source_name, runtime_name
        )
        return TypedImportedFunctionNode(
            node.node,
            node.typ,
            overloads,
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


class _ImportDeclarations:
    """Own declaration operations for this domain."""

    def _load_import_definitions(
        self,
        spec: ImportSpec,
    ):
        """Load import definitions during static analysis."""
        try:
            exports = self.module_loader.load(
                spec.path,
                current_file=self.source_file,
            )
            return exports, spec, import_definitions(exports, spec)
        except ModuleLoadError:
            if spec.components or len(spec.path.parts) < 2:
                raise
            module_path = ImportPath(spec.path.parts[:-1], spec.path.root)
            component = ImportComponent(Symbol(spec.path.parts[-1]))
            split_spec = ImportSpec(module_path, spec.alias, (component,))
            exports = self.module_loader.load(
                split_spec.path,
                current_file=self.source_file,
            )
            return exports, split_spec, import_definitions(exports, split_spec)

    def _register_imported_definition(
        self,
        name: Symbol,
        typed_node: TypedFunctionNode,
        runtime_name: Symbol,
    ) -> None:
        """Register imported definition during static analysis."""
        declared = tuple(
            typing.overload
            for typing in typed_node.overloads
            if isinstance(typing.overload, T.Overload)
        )
        for selected in _functions._callable_overloads(typed_node.typ):
            overload = next(
                (
                    candidate
                    for candidate in declared
                    if candidate.params == selected.params
                    and candidate.returns == selected.returns
                ),
                selected,
            )
            self.env.define_overload(name, overload)
            self.env.bind_runtime_name(name, runtime_name, overload)

    def _register_imported_friendly_runtime_elements(
        self,
        owner: Symbol,
        definitions: tuple[DefineNode, ...],
    ) -> None:
        """Make hidden native calls in imported friendly wrappers type-visible."""
        for definition in definitions:
            body = definition.function.body
            if not body or not isinstance(body[-1], ElementNode):
                continue
            runtime_name = body[-1].name
            if not runtime_name.namespace or self.env.overloads_for(runtime_name):
                continue
            params = tuple(
                param.typ or T.Any for param in definition.function.params or ()
            )
            self.env.define_overload(
                runtime_name,
                T.Overload((T.N(owner), *params), definition.function.returns or ()),
            )

    def _register_imported_object(
        self,
        obj,
        runtime_name: Symbol,
    ) -> None:
        """Register imported object during static analysis."""
        self.env.bind_runtime_name(obj.name, runtime_name)
        node = obj.typed.node
        if not isinstance(node, ObjectNode):
            return
        kind = node.kind.text
        if kind == "trait":
            self._define_trait_shape(obj.name, node)
            return

        if kind != "object" or node.target is not None:
            return

        object_attributes = self._object_attributes(node.fields, node.generics)
        if object_attributes is None:
            return
        defaults = frozenset(field.name for field in node.fields if field.default)
        constructors = constructor_definitions(obj.name, node.definitions)
        friendly_definitions = tuple(
            definition
            for definition in node.definitions
            if definition not in constructors
        )
        self._define_object_shape(
            obj.name,
            node,
            object_attributes,
            defaults=defaults,
            synthesize_constructor=not constructors,
        )
        current = AnalysisBranch()
        for constructor in constructors:
            current = self._register_constructor_definition(
                current,
                node,
                constructor,
                defaults,
            )
        if obj.import_friendly:
            self._register_imported_friendly_runtime_elements(
                obj.name,
                friendly_definitions,
            )
            self._register_friendly_definitions(
                current,
                obj.name,
                friendly_definitions,
            )

