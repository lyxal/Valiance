"""Focused variants declaration analysis."""

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
class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

class _VariantDeclarations:
    """Own variants declaration operations."""

    def _variant_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build the definition for variant during static analysis."""
        generic_constraints = _functions._generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        requirements = _utils._trait_requirements(node)
        members: list[Symbol] = []
        for member in node.variants:
            member_name = _utils._child_symbol(node.name, member.name)
            members.append(member_name)
            object_attributes = self._object_attributes(member.fields, node.generics)
            if object_attributes is None:
                return BranchSet((branch.emit(TypedNode(node, None)),))
            variant_type = _utils._declared_nominal(node.name, node.generics)
            self._define_object_shape(
                member_name,
                node,
                object_attributes,
                result_type=variant_type,
                generic_constraints=generic_constraints,
            )
            self.env.define_overload(
                member.name,
                T.Overload(
                    params=tuple(attribute.typ for attribute in object_attributes),
                    returns=(variant_type,),
                    generic_constraints=generic_constraints,
                ),
            )
        self.env.define_variant(
            node.name,
            tuple(members),
            generics=node.generics,
            generic_variance=_functions._declared_or_inferred_variance(
                node.generics,
                node.generic_variances,
                (),
                requirements,
                self.env.context,
            ),
            requirements=requirements,
        )
        if annotation_hooks.has_annotation(node.annotations, "errType"):
            self.env.add_trait_impl(node.name, Symbol("Err"))

        current = branch.emit(
            TypedNode(node, _utils._declared_nominal(node.name, node.generics))
        )
        requirements_by_name = {
            requirement.name: requirement for requirement in requirements
        }
        for member, member_name in zip(node.variants, members, strict=True):
            definitions_by_name: dict[Symbol, list[DefineNode]] = {}
            for definition in member.definitions:
                definitions_by_name.setdefault(definition.name, []).append(definition)

            for requirement in requirements:
                implementations = definitions_by_name.get(requirement.name, ())
                if not implementations:
                    self._diagnose(
                        f"variant member '{member.name}' must implement element "
                        f"'{requirement.name}'",
                        member,
                    )
                elif len(implementations) > 1:
                    self._diagnose(
                        f"variant member '{member.name}' must implement element "
                        f"'{requirement.name}' exactly once",
                        member,
                    )

            for definition in member.definitions:
                current = self._register_variant_member_definition(
                    current,
                    node,
                    member_name,
                    definition,
                    requirements_by_name.get(definition.name),
                )

        return BranchSet((current,))

    def _register_variant_member_definition(
        self,
        branch: AnalysisBranch,
        variant: ObjectNode,
        owner: Symbol,
        definition: DefineNode,
        requirement: T.TraitRequirement | None,
    ) -> AnalysisBranch:
        """Register variant member definition during static analysis."""
        if requirement is None:
            return self._register_friendly_definition(branch, owner, definition)
        if not self._validate_annotations(definition.annotations, "define", definition):
            return branch.emit(TypedNode(definition, None))

        explicit_params = tuple(definition.function.params or ())
        if len(explicit_params) != len(requirement.overload.params):
            self._diagnose(
                f"variant member element '{definition.name}' must take "
                f"{len(requirement.overload.params)} explicit parameter(s), got "
                f"{len(explicit_params)}",
                definition,
            )
            return branch.emit(TypedNode(definition, None))

        contextual_params: list[FunctionParam] = []
        for source, required in zip(
            explicit_params,
            requirement.overload.params,
            strict=True,
        ):
            if source.typ is not None and not T.same(source.typ, required):
                self._diagnose(
                    f"variant member element '{definition.name}' parameter type "
                    f"{T.show(source.typ)} does not match required type "
                    f"{T.show(required)}",
                    definition,
                )
                return branch.emit(TypedNode(definition, None))
            contextual_params.append(replace(source, typ=required))

        if definition.function.returns is not None and (
            len(definition.function.returns) != len(requirement.overload.returns)
            or any(
                not T.same(actual, required)
                for actual, required in zip(
                    definition.function.returns,
                    requirement.overload.returns,
                    strict=False,
                )
            )
        ):
            self._diagnose(
                f"variant member element '{definition.name}' return signature "
                "does not match the variant extend declaration",
                definition,
            )
            return branch.emit(TypedNode(definition, None))

        self_type = _utils._declared_nominal(owner, variant.generics)
        params = (FunctionParam(Symbol("self"), self_type), *contextual_params)
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            FunctionNode(
                params=params,
                body=definition.function.body,
                returns=requirement.overload.returns,
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
            (*variant.generics, *definition.generics),
        )
        function_node = replace(
            function_node,
            generics=(*variant.generics, *definition.generics),
            generic_variances=(
                *variant.generic_variances,
                *definition.generic_variances,
            ),
            generic_constraints=(
                *variant.generic_constraints,
                *definition.generic_constraints,
            ),
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
        [typing] = function.overloads
        if not isinstance(typing.overload, T.Overload):
            return branch.emit(TypedNode(definition, None))

        generic_constraints = (
            *_functions._generic_constraints(
                variant.generics,
                variant.generic_variances,
                variant.generic_constraints,
            ),
            *_functions._generic_constraints(
                definition.generics,
                definition.generic_variances,
                definition.generic_constraints,
            ),
        )
        concrete = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
            _functions._with_generic_constraints(
                typing.overload,
                generic_constraints,
            ),
            definition.annotations,
        )
        variant_type = _utils._declared_nominal(variant.name, variant.generics)
        exposed = replace(
            concrete,
            params=(variant_type, *requirement.overload.params),
            returns=requirement.overload.returns,
            param_names=(None, *requirement.overload.param_names),
            is_multi=True,
        )
        self.env.define_overload(
            definition.name,
            exposed,
            object_friendly=True,
        )
        self.env.define_overload(
            Symbol(f"{owner}::{definition.name}"),
            replace(concrete, is_multi=False),
        )
        return typed_branch

