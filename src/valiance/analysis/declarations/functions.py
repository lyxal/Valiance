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
        # A complete declared signature is an interface in its own right.
        # Publish it before body analysis so recursion and subsequent recovery
        # do not depend on finding a particular reference shape in the body.
        declared_overload = _functions._fully_typed_overload(function_node)
        declared_index: int | None = None
        if declared_overload is not None:
            declared_index = next(
                (
                    index
                    for index, candidate in enumerate(
                        self.env.overloads.get(name, ())
                    )
                    if candidate == declared_overload
                    and index
                    not in self.env.object_friendly_overloads.get(name, set())
                ),
                None,
            )
            if declared_index is None:
                if not self._define_overload_with_diagnostic(
                    name,
                    declared_overload,
                    node,
                ):
                    return BranchSet((branch.emit(TypedNode(node, None)),))
                declared_index = len(self.env.overloads.get(name, ())) - 1
        result = self._analyse_overloaded_function_literal(
            branch,
            function_node,
            node,
            allow_top_level_captures=False,
        )
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
            if declared_index is not None and typing_index == 0:
                # Replace the provisional interface with its validated form so
                # inferred constraints, effects, tags, and runtime metadata do
                # not create a duplicate overload.
                self.env.overloads[name][declared_index] = overload
                original_index = declared_index
            else:
                if not self.env.has_local_non_object_friendly_overload(name, overload):
                    if not self._define_overload_with_diagnostic(name, overload, node):
                        return BranchSet(
                            (typed_branch.emit(TypedNode(node, None)),)
                        )
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
                        if not self._define_overload_with_diagnostic(
                            name,
                            generated,
                            node,
                        ):
                            return BranchSet(
                                (typed_branch.emit(TypedNode(node, None)),)
                            )
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

    def _define_overload_with_diagnostic(
        self,
        name: Symbol,
        overload: T.Overload,
        node: DefineNode,
    ) -> bool:
        """Register an overload or turn a shape conflict into a diagnostic."""
        try:
            self.env.define_overload(name, overload)
        except ValueError as exc:
            self._diagnose(
                self._definition_overload_diagnostic(
                    str(exc),
                    name,
                    overload,
                ),
                node,
            )
            return False
        return True

    def _definition_overload_diagnostic(
        self,
        message: str,
        name: Symbol,
        overload: T.Overload,
    ) -> str:
        """Add concrete fixes for a local definition's overload conflict."""
        imported = self._imported_definition_sources.get(name)
        visible = self.env.overloads_for(name)
        shape_help = ""
        if visible:
            existing = visible[0]
            if len(existing.params) != len(overload.params):
                expected = len(existing.params)
                shape_help = (
                    f"\nhelp: or change the local definition to take "
                    f"{expected} {_counted_word(expected, 'input')}"
                )
            elif len(existing.returns) != len(overload.returns):
                expected = len(existing.returns)
                shape_help = (
                    f"\nhelp: or change the local definition to return "
                    f"{expected} {_counted_word(expected, 'value')}"
                )
        if imported is None:
            return (
                f"{message}\n"
                f"help: rename the local definition `{name}` so each "
                f"overload set has a consistent shape"
                f"{shape_help}"
            )
        module = imported.rsplit(".", 1)[0]
        namespace = module.rsplit(".", 1)[-1]
        return (
            f"{message}\n"
            f"help: either rename the local definition `{name}` or remove "
            f"the import `{imported}`\n"
            f"help: or keep the import namespaced with "
            f"`import {{ {module} }}` and use `{namespace}.{name.text}`"
            f"{shape_help}"
        )

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



def _counted_word(count: int, singular: str) -> str:
    """Return a noun inflected for ``count`` in analyser help text."""
    return singular if count == 1 else f"{singular}s"
