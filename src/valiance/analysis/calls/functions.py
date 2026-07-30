"""Focused functions call analysis."""

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

from . import candidates as _calls
from . import callable_values as _functions
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from .. import analyser as _core
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
from .models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






class _CallableValues:
    """Own functions operations for call planning."""

    def _overload_function_variants(
        self,
        node: FunctionNode,
        origin: ASTNode,
    ) -> tuple[FunctionNode, ...] | None:
        """Expand explicit overload signatures into fully typed function nodes."""
        if not node.overloads:
            return (node,)

        source_params = node.params
        variants: list[FunctionNode] = []
        has_declared_signature = node.returns is not None or any(
            param.typ is not None for param in source_params or ()
        )
        if has_declared_signature:
            variants.append(replace(node, overloads=()))

        for signature in node.overloads:
            if source_params is not None and len(source_params) != len(signature.params):
                self._diagnose(
                    "overload signature has "
                    f"{len(signature.params)} parameter type(s), but the following "
                    f"function declares {len(source_params)} parameter(s)",
                    origin,
                )
                return None
            params = tuple(
                replace(param, typ=typ)
                for param, typ in zip(source_params, signature.params, strict=True)
            ) if source_params is not None else tuple(
                FunctionParam(None, typ) for typ in signature.params
            )
            variants.append(
                replace(
                    node,
                    params=params,
                    returns=signature.returns,
                    overloads=(),
                )
            )
        return tuple(variants)

    def _analyse_overloaded_function_literal(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
        origin: ASTNode,
        *,
        allow_top_level_captures: bool = True,
    ) -> tuple[FunctionAnalysis, AnalysisBranch] | None:
        """Analyse every explicitly declared signature against one shared body."""
        variants = self._overload_function_variants(node, origin)
        if variants is None:
            return None
        if len(variants) == 1 and variants[0] is node:
            return self._analyse_function_literal(
                outer,
                node,
                allow_top_level_captures=allow_top_level_captures,
            )

        typings: list[FunctionOverloadTyping] = []
        for variant in variants:
            genericized = _functions._genericize_function_node(
                variant,
                variant.generics,
            )
            result = self._analyse_function_literal(
                outer,
                genericized,
                allow_top_level_captures=allow_top_level_captures,
            )
            if result is None:
                return None
            analysis, _ = result
            typings.extend(analysis.overloads)

        overloads = tuple(
            typing.overload
            for typing in typings
            if isinstance(typing.overload, T.Overload)
        )
        if len(overloads) != len(typings):
            self._diagnose("overload signatures must produce concrete function types", origin)
            return None
        typ = (
            T.Fn(overloads[0].params, overloads[0].returns, overloads[0].element_tags)
            if len(overloads) == 1 and not overloads[0].where_clause
            else T.Overloads(*overloads)
        )
        return FunctionAnalysis(typ, tuple(typings)), outer

    def _analyse_function_literal(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
        *,
        initial_function_locals: tuple[tuple[Symbol, T.Type], ...] = (),
        allow_top_level_captures: bool = True,
    ) -> tuple[FunctionAnalysis, AnalysisBranch] | None:
        """Analyse function literal during static analysis."""
        declared_params = _functions._declared_params(node)
        _, where_error = static_where.validate_where_clause(
            params=declared_params,
            returns=node.returns or (),
            param_names=_functions._function_param_names_for_overload(
                node, declared_params
            ),
            clause=node.where_clause,
        )
        if where_error is not None:
            self._diagnose(
                f"invalid where clause: {where_error.message}",
                where_error.node or node,
            )
            return None
        node = _functions._contextualize_function_empty_returns(node)
        if node.params is not None and any(
            _functions._is_call_site_checked_param(param.typ) for param in node.params
        ):
            return self._call_site_checked_function(outer, node), outer

        top_level_captures = (
            ()
            if allow_top_level_captures
            else _functions._top_level_capture_nodes(outer, node)
        )
        if top_level_captures:
            for capture in top_level_captures:
                self._diagnose(
                    f"cannot capture top-level variable '{capture.name}'",
                    capture,
                )
            return None

        parameter_writes = _functions._parameter_write_nodes(node)
        if parameter_writes:
            for write, name in parameter_writes:
                self._diagnose(
                    f"cannot assign to read-only parameter '{name}'",
                    write,
                )
            return None

        params = _functions._declared_params(node)
        body_params = tuple(
            _functions._parameter_value_type(
                _functions._anonymous_trait_subject_view(param)
            )
            for param in params
        )
        if node.params is None:
            mode = InputMode.INFER_INPUTS
        elif not node.params:
            mode = InputMode.NILADIC
        else:
            mode = InputMode.CYCLE_EXPLICIT_PARAMS
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(node.params or (), body_params, strict=True)
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_functions._function_capture_source(
                outer,
                allow_top_level_assignments=allow_top_level_captures,
            ),
        )
        for local_name, local_type in initial_function_locals:
            write = variables.write(local_name, local_type, ctx=self.env.context)
            if write.variables is None:
                diagnostic = write.diagnostic or f"cannot define '{local_name}'"
                self._diagnose(diagnostic, node)
                return None
            variables = write.variables
        recursive_overload = annotation_hooks.recursive_overload(node, params)
        if annotation_hooks.has_annotation(node.annotations, "recursive"):
            if recursive_overload is None:
                self._diagnose(
                    "@recursive requires explicit parameter and return types",
                    node,
                )
                return None
            write = variables.write(
                Symbol("this"),
                T.Fn(
                    recursive_overload.params,
                    recursive_overload.returns,
                    recursive_overload.element_tags,
                ),
                block_local=False,
            )
            if write.variables is None:
                return None
            variables = write.variables
        for name in _functions._static_body_variable_names(node):
            write = variables.write(
                name,
                T.Number,
                block_local=False,
            )
            if write.variables is None:
                diagnostic = write.diagnostic or f"cannot define '{name}'"
                self._diagnose(diagnostic, node)
                return None
            variables = write.variables
        initial_stack = T.TypeStack(
            tuple(
                typ
                for param, typ in zip(node.params or (), body_params, strict=True)
                if param.name is None
            )
            if mode is InputMode.CYCLE_EXPLICIT_PARAMS
            else ()
        )
        initial = AnalysisBranch(
            stack=initial_stack,
            inputs=body_params if mode is not InputMode.INFER_INPUTS else (),
            input_names=(
                tuple(param.name for param in node.params or ())
                if mode is not InputMode.INFER_INPUTS
                else ()
            ),
            variables=variables,
            input_mode=mode,
            cycle_params=body_params if mode is InputMode.CYCLE_EXPLICIT_PARAMS else (),
            cycle_stack_remaining=(
                len(body_params) if mode is InputMode.CYCLE_EXPLICIT_PARAMS else 0
            ),
            cycle_from_top=mode is InputMode.CYCLE_EXPLICIT_PARAMS,
            atomic_type_vars=(
                outer.atomic_type_vars
                | _functions._atomic_parameter_type_vars(params)
            ),
            origin=outer.origin,
        )

        generic_constraints = _functions._generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        structural_overloads = _functions._anonymous_trait_overloads(
            *params,
            *(constraint.bound for constraint in generic_constraints),
        )
        function_env = self.env.lexical_child_scope()
        for name, overload in structural_overloads:
            function_env.overloads.setdefault(name, []).append(overload)
        if recursive_overload is not None and annotation_hooks.has_annotation(
            node.annotations,
            "recursive",
        ):
            function_env.define_overload(Symbol("this"), recursive_overload)
        function_analyser = self._child_analyser(function_env)
        final = function_analyser.analyse_block(BranchSet((initial,)), node.body)
        signatures = self._function_signatures(node, final)
        analysis = _functions._function_analysis_from_signatures(signatures)
        if analysis is None:
            if (
                node.params is not None
                and any(param.typ is None for param in node.params)
                and not function_analyser.diagnostics
            ):
                self.warnings.extend(function_analyser.warnings)
                self._extend_lint_findings(function_analyser.lint_findings)
                return self._call_site_checked_function(outer, node), outer
            self.diagnostics.extend(function_analyser.diagnostics)
            self.warnings.extend(function_analyser.warnings)
            self._extend_lint_findings(function_analyser.lint_findings)
            return None
        self.warnings.extend(function_analyser.warnings)
        self._extend_lint_findings(function_analyser.lint_findings)
        return analysis, outer

    def _call_site_checked_function(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
    ) -> FunctionAnalysis:
        """Compute call site checked function during static analysis."""
        params = _functions._declared_params(node)
        overload = _functions._function_overload(
            node,
            params=params,
            returns=(),
            call_site_body=(outer, node),
        )
        typ = T.Overloads(overload)
        return FunctionAnalysis(
            typ,
            (FunctionOverloadTyping(T.Fn(params, ()), (), overload),),
        )

    def _analyse_function_at_call_site(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
        call_params: tuple[T.Type, ...],
        *,
        rank_values: dict[str, int] | None = None,
        type_values: dict[str, T.Type] | None = None,
        where_evaluated: bool = False,
        static_values: dict[str, int] | None = None,
    ) -> FunctionAnalysis | None:
        """Analyse a deferred function using call-site static bindings."""
        typed_node = cast(
            FunctionNode,
            _functions._substitute_rank_variables_in_ast(
                node, {}, type_values
            ),
        )
        parameter_node = cast(
            FunctionNode,
            _functions._substitute_rank_variables_in_ast(
                node, rank_values or {}, type_values
            ),
        )
        declared = tuple(parameter_node.params or ())
        if len(call_params) < len(declared):
            return None
        substituted_params = _functions._call_site_substituted_params(
            declared,
            call_params[-len(declared) :] if declared else (),
            self.env.context,
        )
        if substituted_params is None:
            return None
        call_site_node = FunctionNode(
            params=substituted_params,
            body=tuple(
                _core._resolve_pop_n_static_counts(item, static_values or {})
                for item in typed_node.body
            ),
            returns=typed_node.returns,
            where_clause=() if where_evaluated else typed_node.where_clause,
            element_tags=typed_node.element_tags,
            element_tags_explicit=typed_node.element_tags_explicit,
            companion_tags_allowed=typed_node.companion_tags_allowed,
            location=typed_node.location,
        )
        explicit_count = len(declared)
        stack_params = call_params[:-explicit_count] if explicit_count else call_params
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(
                substituted_params,
                call_params[-explicit_count:] if explicit_count else (),
                strict=False,
            )
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_functions._function_capture_source(outer),
        )
        for name in _functions._static_body_variable_names(node):
            write = variables.write(
                name,
                T.Number,
                block_local=False,
            )
            if write.variables is None:
                return None
            variables = write.variables
        initial = AnalysisBranch(
            stack=T.TypeStack(stack_params),
            inputs=call_params,
            variables=variables,
            input_mode=InputMode.NILADIC,
            atomic_type_vars=(
                outer.atomic_type_vars
                | _functions._atomic_parameter_type_vars(call_params)
            ),
            origin=outer.origin,
        )
        function_analyser = self._child_analyser(self.env.lexical_child_scope())
        final = function_analyser.analyse_block(
            BranchSet((initial,)), call_site_node.body
        )
        signatures = self._function_signatures(call_site_node, final)
        return _functions._function_analysis_from_signatures(signatures)

    def _function_signatures(
        self,
        node: FunctionNode,
        branches: BranchSet,
    ) -> dict[T.Overload, tuple[TypedNode, ...]]:
        """Build the signatures for function during static analysis."""
        signatures: dict[T.Overload, tuple[TypedNode, ...]] = {}
        surviving_element_tags = frozenset(
            tag for branch in branches for tag in branch.element_tags
        )
        surviving_data_element_uses = frozenset(
            use for branch in branches for use in branch.data_element_uses
        )
        self._validate_recorded_data_element_uses(
            surviving_data_element_uses,
            node,
        )
        return_all = annotation_hooks.has_annotation(node.annotations, "returnAll")

        def selected_return_count(branch: AnalysisBranch) -> int | None:
            """Return the observable multiplicity of one explicit return path."""
            if branch.failed or branch.return_stack is None:
                return None
            if branch.return_exact or return_all:
                return len(branch.return_stack)
            if node.returns is not None:
                return len(node.returns)
            return min(len(branch.return_stack), 1)

        explicit_counts = {
            count
            for branch in branches
            if (count := selected_return_count(branch)) is not None
        }
        if len(explicit_counts) > 1:
            counts = ", ".join(str(count) for count in sorted(explicit_counts))
            self._diagnose(
                "function return branches must return the same number of values; "
                f"got {counts}",
                node,
            )
            return {}
        if node.returns is None and not return_all and any(
            count > 1 for count in explicit_counts
        ):
            self._diagnose(
                "inferred-return functions may return at most one value; "
                "declare return types or use @returnAll for multiple values",
                node,
            )
            return {}
        for branch in branches:
            if branch.failed:
                continue
            refined = self._function_returns(node, branch)
            if refined is None:
                continue
            returns, branch = refined
            body_element_tags = surviving_element_tags
            final_element_tags = _functions._final_function_element_tags(
                node,
                body_element_tags,
                self.env,
            )
            self._validate_inferred_element_tags(
                node,
                body_element_tags,
                final_element_tags,
            )
            declared_params = _functions._declared_params(node)
            inputs = (
                declared_params
                if node.params is not None
                and any(
                    _functions._contains_anonymous_trait(param)
                    for param in declared_params
                )
                else branch.inputs
            )
            inputs = _functions._restore_parameter_markers(
                declared_params,
                inputs,
            )
            self._validate_data_element_tag_disjoints(
                inputs,
                final_element_tags,
                node,
            )
            signature = _functions._function_overload(
                node,
                params=inputs,
                returns=returns,
                where_clause=node.where_clause,
                element_tags=final_element_tags,
            )
            signatures.setdefault(signature, branch.typed_body)

        if len(signatures) <= 1:
            return signatures
        return {
            signature: body
            for signature, body in signatures.items()
            if not _utils._has_never_return(signature)
        }

    def _function_returns(
        self,
        node: FunctionNode,
        branch: AnalysisBranch,
    ) -> tuple[tuple[T.Type, ...], AnalysisBranch] | None:
        """Determine the return types for function during static analysis."""
        result_stack = branch.return_stack if branch.return_stack is not None else branch.stack
        if branch.return_stack is not None and branch.return_exact:
            if node.returns is None:
                return result_stack.items, branch
        elif annotation_hooks.has_annotation(node.annotations, "returnAll"):
            return result_stack.items, branch
        elif node.returns is None:
            return (result_stack.items[-1:] if result_stack else ()), branch

        checked_returns = tuple(_utils._return_value_shape(typ) for typ in node.returns)
        expected = T.TypeStack(checked_returns)
        actual_returns = _utils._stack_returns(result_stack, expected)
        if len(actual_returns) != len(node.returns):
            return None
        substitution = _calls._branch_argument_substitution(
            actual_returns,
            checked_returns,
            self.env.context,
        )
        if (
            substitution is None
            and node.where_clause
            and _functions._contains_rank_var(node.returns)
        ):
            return node.returns, branch
        if substitution is not None:
            branch = _calls._specialize_branch_arguments(branch, substitution)
            result_stack = (
                branch.return_stack
                if branch.return_stack is not None
                else branch.stack
            )
        if not _utils._stack_assignable(result_stack, expected, self.env.context):
            if node.where_clause and _functions._contains_rank_var(node.returns):
                return node.returns, branch
            for actual, declared in zip(actual_returns, checked_returns, strict=True):
                if _functions._is_result_type(actual) and not _functions._is_result_type(declared):
                    self._diagnose(
                        "function body can return "
                        f"{T.show(actual)}, but the explicit return annotation is "
                        f"{T.show(declared)}; declare a compatible Result return type",
                        node,
                    )
                    break
            return None
        return node.returns, branch

