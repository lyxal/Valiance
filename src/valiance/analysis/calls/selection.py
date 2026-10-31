"""Focused selection call analysis."""

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
from itertools import count, product
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
    TypedFunctionDispatchNode,
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

from . import candidates as _calls
from . import callable_values as _functions
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
from .models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






class _CallSelection:
    """Own selection operations for call planning."""

    def select_call_winners(
        self,
        *,
        candidates: Iterable[CallCandidate],
        branch: AnalysisBranch,
        node: ASTNode,
        no_match_message: str | Callable[[], str],
        ambiguous_message: str | Callable[[], str],
    ) -> tuple[CallCandidate, ...] | None:
        """Select the most specific viable call candidates and diagnose ambiguity."""
        candidates = tuple(candidates)
        # A total conversion must remain usable from a generic function. Prefer
        # providers accepting its source contract over providers that only match
        # by specializing that contract (for example, T to &CString).
        accepting_targets = {
            candidate.applied.overload.conversion_target
            for candidate in candidates
            if candidate.applied.overload.conversion_target is not None
            and candidate.source_arguments
            and all(
                T.assignable(source, parameter, self.env.context)
                for source, parameter in zip(
                    candidate.source_arguments, candidate.applied.params, strict=True
                )
            )
        }
        candidates = tuple(
            candidate for candidate in candidates
            if candidate.applied.overload.conversion_target not in accepting_targets
            or all(
                T.assignable(source, parameter, self.env.context)
                for source, parameter in zip(
                    candidate.source_arguments, candidate.applied.params, strict=True
                )
            )
        )
        provider_ambiguity = self._behaviour_set_provider_ambiguity(
            candidates,
            branch,
        )
        if provider_ambiguity is not None:
            source_name, trait_name, providers = provider_ambiguity
            choices = ", ".join(
                f"`as[{provider}.{trait_name}]`" for provider in providers
            )
            self._diagnose(
                f"ambiguous implementation of {trait_name} for {source_name}\n"
                "candidate behaviour sets:\n"
                + "\n".join(
                    f"  {provider}.{trait_name}" for provider in providers
                )
                + f"\nhelp: qualify the value with one of {choices}",
                node,
            )
            return None
        winners = _functions._collapse_equivalent_call_winners(
            _functions._collapse_equivalent_friendly_multidispatch_winners(
                _functions._best_candidates(candidates, branch)
            )
        )
        winners = self._prefer_strict_declared_subtype(winners)
        winners = self._prefer_latest_equal_winners(winners)
        if not winners:
            provider_ambiguity = self._overload_provider_ambiguity(
                node,
                branch,
            )
            if provider_ambiguity is not None:
                source_name, trait_name, providers = provider_ambiguity
                choices = ", ".join(
                    f"`as[{provider}.{trait_name}]`" for provider in providers
                )
                self._diagnose(
                    f"ambiguous implementation of {trait_name} for {source_name}\n"
                    "candidate behaviour sets:\n"
                    + "\n".join(
                        f"  {provider}.{trait_name}" for provider in providers
                    )
                    + f"\nhelp: qualify the value with one of {choices}",
                    node,
                )
            else:
                message = (
                    no_match_message()
                    if callable(no_match_message)
                    else no_match_message
                )
                self._diagnose(
                    message,
                    node,
                )
            return None
        if (
            len(winners) > 1
            and branch.input_mode is not InputMode.INFER_INPUTS
            and not _functions._winners_specialize_inputs(winners, branch)
        ):
            message = (
                ambiguous_message()
                if callable(ambiguous_message)
                else ambiguous_message
            )
            self._diagnose(
                f"{message}\n"
                f"candidate overloads:\n{_utils._show_applied_overloads(winners)}",
                node,
            )
            return None
        return winners




    def _overload_provider_ambiguity(
        self,
        node: ASTNode,
        branch: AnalysisBranch,
    ) -> tuple[Symbol, Symbol, tuple[Symbol, ...]] | None:
        """Find ambiguous trait evidence among rejected named overloads."""
        name = getattr(node, "name", None)
        if not isinstance(name, Symbol):
            return None
        for overload in self.env.overloads_for(name):
            if len(overload.params) > len(branch.stack):
                continue
            actuals = branch.stack.items[-len(overload.params):] if overload.params else ()
            for actual, parameter in zip(actuals, overload.params):
                if not isinstance(actual, T.NominalType):
                    continue
                trait_names: list[Symbol] = []
                if isinstance(parameter, T.NominalType):
                    if not parameter.name.namespace:
                        trait_names.append(parameter.name)
                elif isinstance(parameter, T.VarType):
                    trait_names.extend(
                        constraint.bound.name
                        for constraint in overload.generic_constraints
                        if constraint.name == parameter.name
                        and isinstance(constraint.bound, T.NominalType)
                        and not constraint.bound.name.namespace
                    )
                for trait_name in trait_names:
                    providers = tuple(
                        sorted(
                            self.env.context.implementation_providers(
                                actual.name,
                                trait_name,
                            ),
                            key=str,
                        )
                    )
                    if len(providers) > 1:
                        return actual.name, trait_name, providers
        return None

    def _behaviour_set_provider_ambiguity(
        self,
        candidates: tuple[CallCandidate, ...],
        branch: AnalysisBranch,
    ) -> tuple[Symbol, Symbol, tuple[Symbol, ...]] | None:
        """Find an unqualified trait relationship with competing providers."""
        for candidate in candidates:
            for actual, parameter in zip(branch.stack, candidate.applied.params):
                if not isinstance(actual, T.NominalType):
                    continue
                if not isinstance(parameter, T.NominalType):
                    continue
                if parameter.name.namespace:
                    continue
                providers = tuple(
                    sorted(
                        self.env.context.implementation_providers(
                            actual.name,
                            parameter.name,
                        ),
                        key=str,
                    )
                )
                if len(providers) > 1:
                    return actual.name, parameter.name, providers
        return None

    def _prefer_strict_declared_subtype(
        self, winners: tuple[CallCandidate, ...]
    ) -> tuple[CallCandidate, ...]:
        """Use declared parameter specificity when instantiation erases it.

        Call-site checked overloads specialize their parameter types to the
        arguments before scoring. Consequently, `Int~` and `Real~` can both
        look like an exact `Int+2` match. Only break such a tie when one
        *declared* parameter list is strictly narrower than another, and the
        applied arguments and source branch are otherwise identical.
        """
        if len(winners) < 2:
            return winners

        def narrower(left: CallCandidate, right: CallCandidate) -> bool:
            """Test strict declared-parameter specificity for otherwise identical candidates."""
            if (
                left.branch != right.branch
                or left.applied.params != right.applied.params
                or len(left.applied.overload.params)
                != len(right.applied.overload.params)
            ):
                return False
            left_params = left.applied.overload.params
            right_params = right.applied.overload.params
            return (
                all(
                    T.assignable(a, b, self.env.context)
                    for a, b in zip(left_params, right_params, strict=True)
                )
                and any(
                    not T.assignable(b, a, self.env.context)
                    for a, b in zip(left_params, right_params, strict=True)
                )
            )

        return tuple(
            item for item in winners
            if not any(
                other is not item and narrower(other, item)
                for other in winners
            )
        )

    @staticmethod
    def _prefer_latest_equal_winners(
        winners: tuple[CallCandidate, ...],
    ) -> tuple[CallCandidate, ...]:
        """Prefer the latest declaration for an equivalent invocation.

        Specificity selection has already removed dominated candidates. This
        pass only combines candidates whose applied parameters, returns, branch,
        argument plan, and dispatch priority are otherwise identical. Distinct
        generic inference paths and genuinely ambiguous overloads remain intact.
        """
        selected: list[CallCandidate] = []
        for candidate in winners:
            equivalent_index = next(
                (
                    index
                    for index, existing in enumerate(selected)
                    if candidate.applied.params == existing.applied.params
                    and candidate.applied.actual_returns
                    == existing.applied.actual_returns
                    and candidate.branch == existing.branch
                    and candidate.call_arg_order == existing.call_arg_order
                    and candidate.dispatch_priority == existing.dispatch_priority
                ),
                None,
            )
            if equivalent_index is None:
                selected.append(candidate)
                continue
            existing = selected[equivalent_index]
            selected[equivalent_index] = candidate
        return tuple(selected)

    def element_call_candidates(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        sources: Iterable[ElementArguments],
    ) -> list[CallCandidate]:
        """Build viable typed candidates for an explicit element call."""
        candidates: list[CallCandidate] = []
        for source in sources:
            candidate = _calls._apply_overload_to_branch(
                source.overload,
                source.arguments,
                source.branch,
                self.env.context,
                self.env,
                node.disambiguation,
                self,
                node.generic_args,
            )
            if candidate is None or candidate.branch.inputs != source.branch.inputs:
                variants = _calls._inferred_call_site_argument_variants(source)
                if variants:
                    candidates.extend(self.element_call_candidates(node, overloads, variants))
                    continue
            if candidate is None:
                candidate = _calls._apply_overload_via_unit_overlay(
                    node.name,
                    source.overload,
                    source.arguments,
                    source.branch,
                    self.env.context,
                    self.env,
                    node.disambiguation,
                    self,
                    node.generic_args,
                )
            if candidate is None:
                continue

            applied = _calls._apply_tag_overlay(
                node.name,
                source.arguments,
                candidate.applied,
                self.env.context,
                self.env,
            )
            selected_is_friendly = self.env.overload_is_object_friendly(
                node.name,
                source.implementation_id,
            )
            dispatch_overloads = tuple(
                overload
                for index, overload in enumerate(overloads)
                if self.env.overload_is_object_friendly(node.name, overload.implementation_id)
                == selected_is_friendly
            )
            applied = _functions._mark_multidispatch(
                applied,
                dispatch_overloads,
                self.env.context,
            )
            candidates.append(
                CallCandidate(
                    applied=applied,
                    branch=candidate.branch,
                    modifiers=source.modifiers,
                    call_arg_order=source.call_arg_order,
                    implementation_id=source.implementation_id,
                    source_arguments=source.arguments,
                    specialization=candidate.specialization,
                    dispatch_priority=(
                        0
                        if selected_is_friendly
                        else 1
                    ),
                )
            )
        if not candidates:
            covered = self._union_coverage_candidate(node, overloads, tuple(sources))
            if covered is not None:
                candidates.append(covered)
        return candidates

    def _union_coverage_candidate(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        sources: tuple[ElementArguments, ...],
    ) -> CallCandidate | None:
        """Build one runtime-dispatched candidate when overloads cover union inputs."""
        if node.modifier_args or not sources:
            return None
        source = sources[0]
        args = source.arguments
        normalized_args = tuple(T.normalize(arg) for arg in args)
        vector_sources = tuple(
            (index, arg)
            for index, arg in enumerate(normalized_args)
            if isinstance(arg, T.CollectionType)
            and isinstance(T.normalize(arg.base), T.UnionType)
            and isinstance(arg.rank, int)
        )
        if vector_sources:
            ranks = {arg.rank for _, arg in vector_sources}
            if len(ranks) != 1:
                return None
            scalar_args = tuple(
                arg.base if isinstance(arg, T.CollectionType) else arg
                for arg in normalized_args
            )
        else:
            scalar_args = args
        if not any(
            isinstance(T.normalize(arg), T.UnionType) for arg in scalar_args
        ):
            return None
        if any(
            item.arguments != args
            or item.branch != source.branch
            or item.call_arg_order != source.call_arg_order
            for item in sources
        ):
            return None
        declared_overloads = overloads
        specialization = None
        if any(overload.specialize_body for overload in overloads):
            checked = self._checked_union_dispatch(node, overloads, scalar_args, source)
            if checked is None:
                return None
            overloads, specialization = checked
        return_counts = {len(overload.returns) for overload in overloads}
        if len(return_counts) != 1:
            return None
        return_count = next(iter(return_counts))
        expected_returns = tuple(
            T.U(*(overload.returns[index] for overload in overloads))
            for index in range(return_count)
        )
        expected = T.Fn(scalar_args, expected_returns)
        dispatch_overloads = tuple(
            replace(
                overload,
                params=tuple(
                    self._union_dispatch_parameter_view(param, arg)
                    for param, arg in zip(overload.params, scalar_args, strict=True)
                ),
            )
            for overload in overloads
        )
        plan = T.union_dispatched_callable_plan(
            T.Overloads(*dispatch_overloads), expected, self.env.context
        )
        if plan is None:
            return None
        representative = next(overload for overload in overloads
                              if overload.implementation_id == plan.branches[0].implementation_id)
        if specialization is not None:
            original_id = specialization.specializations[0][0]
            representative = next(overload for overload in declared_overloads
                                  if overload.implementation_id == original_id)
        actual_returns = plan.returns
        vectorised = bool(vector_sources)
        vectorised_depths: tuple[int, ...] = ()
        vectorised_target_ranks: tuple[int | None, ...] = ()
        if vectorised:
            rank = vector_sources[0][1].rank
            collection_type = type(vector_sources[0][1])
            actual_returns = tuple(
                T.C(collection_type, result, rank) for result in plan.returns
            )
            vectorised_depths = tuple(
                arg.rank if isinstance(arg, T.CollectionType) else 0
                for arg in normalized_args
            )
            vectorised_target_ranks = tuple(None for _ in scalar_args)
        applied = T.AppliedOverload(
            representative,
            {},
            scalar_args,
            plan.returns,
            actual_returns,
            (),
            vectorised=vectorised,
            vectorised_depths=vectorised_depths,
            union_dispatch_plan=plan,
            vectorised_target_ranks=vectorised_target_ranks,
        )
        return CallCandidate(
            applied=applied,
            branch=source.branch,
            call_arg_order=source.call_arg_order,
            implementation_id=(representative.implementation_id if specialization is not None
                               else plan.branches[0].implementation_id),
            specialization=(replace(specialization, dispatch_plan=plan, vectorised=vectorised,
                                    vectorised_depths=vectorised_depths,
                                    vectorised_target_ranks=vectorised_target_ranks)
                            if specialization is not None else None),
        )

    def _checked_union_dispatch(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        args: tuple[T.Type, ...],
        source: ElementArguments,
    ) -> tuple[tuple[T.Overload, ...], TypedFunctionDispatchNode] | None:
        """Resolve and check every scalar input combination before dispatch."""
        alternatives = tuple(
            tuple(sorted(typ.items, key=T.show)) if isinstance(typ := T.normalize(arg), T.UnionType)
            else (arg,)
            for arg in args
        )
        concrete = []
        specializations = []
        runtime_names = []
        for index, branch_args in enumerate(product(*alternatives)):
            candidates = self.element_call_candidates(node, overloads, tuple(
                replace(source, overload=overload,
                        implementation_id=overload.implementation_id,
                        arguments=branch_args)
                for overload in overloads
            ))
            winners = _functions._collapse_equivalent_call_winners(
                _functions._collapse_equivalent_friendly_multidispatch_winners(
                    _functions._best_candidates(tuple(candidates), source.branch)
                )
            )
            winners = self._prefer_latest_equal_winners(self._prefer_strict_declared_subtype(winners))
            if len(winners) != 1:
                return None
            winner = winners[0]
            applied = winner.applied
            # Each checked body needs its own link, even when the same generic
            # declaration serves several branches.
            identity = f"{winner.implementation_id}:union:{index}"
            concrete.append(replace(
                applied.overload, params=branch_args, returns=applied.actual_returns,
                generic_constraints=(), generic_params=(), where_clause=(),
                call_site_body=None, specialize_body=False, runtime_static_values=(),
                element_tags=applied.element_tags,
                implementation_id=identity,
            ))
            specializations.append((winner.implementation_id, winner.specialization, applied.runtime_static_values))
            runtime_names.append(
                None if node.name == Symbol("call")
                else self.env.runtime_name_for(node.name, applied.overload)
            )
        return tuple(concrete), TypedFunctionDispatchNode(
            node, specializations=tuple(specializations), runtime_names=tuple(runtime_names),
        )

    @staticmethod
    def _union_dispatch_parameter_view(param: T.Type, arg: T.Type) -> T.Type:
        """Qualify imported nominal parameters from matching union branches."""
        param = T.normalize(param)
        arg = T.normalize(arg)
        alternatives = arg.items if isinstance(arg, T.UnionType) else (arg,)
        if isinstance(param, T.NominalType) and not param.name.namespace:
            matches = tuple(
                item
                for item in alternatives
                if isinstance(item, T.NominalType)
                and item.name.text == param.name.text
                and len(item.args) == len(param.args)
            )
            if len(matches) == 1:
                return replace(param, name=matches[0].name)
        return param

    def commit_element_candidate(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        candidate: CallCandidate,
    ) -> AnalysisBranch | None:
        """Emit the typed node for a selected element-call candidate."""
        overload = candidate.applied.overload
        if overload.annotation_error is not None:
            self._diagnose(overload.annotation_error, node)
            return None

        if overload.annotation_warning is not None:
            self._warn(overload.annotation_warning, node)

        reflection_access = None
        if overload.reflection_operation in {"uninit", "finished?", "finish", "finish?", "finish+args", "finish?+args"}:
            from valiance.asts import TypedConstructionNode
            operation = overload.reflection_operation
            base_operation = operation.removesuffix("+args")
            if not self.reflection_unsafe:
                self._diagnose(f"{base_operation} requires lexical unsafe", node)
                return None
            builder = (candidate.applied.actual_returns[0] if operation == "uninit"
                       else candidate.source_arguments[0])
            owner = T.normalize(builder).args[0]
            owner = _functions._resolve_imported_type_names(owner, self.env)
            if operation == "uninit":
                result = T.N(Symbol("Uninit"), owner)
                candidate = replace(candidate, applied=replace(
                    candidate.applied, returns=(result,), actual_returns=(result,),
                ))
            if not self.require_reflection_permission(owner, node):
                return None
            storage = T.normalize(owner)
            definition = self.env.lookup_object(storage.name) if isinstance(storage, T.NominalType) else None
            if isinstance(storage, T.FFINamedType):
                # Native storage has no language-visible fields.  The builder is
                # finalized through a VM-registered trusted native factory;
                # neither the compiler nor the VM fabricates an FFI pointer.
                if storage.args:
                    self._diagnose("native construction requires a concrete FFI type", node)
                    return None
            elif operation.endswith("+args"):
                self._diagnose("construction arguments require native opaque storage", node)
                return None
            elif definition is None or len(storage.args) != len(definition.generics):
                self._diagnose("construction requires a concrete managed object or native type", node)
                return None
            reflection_access = TypedConstructionNode(
                node, candidate.applied.actual_returns[0], self.env.field_metadata(owner),
                base_operation in {"finish", "finish?"}, operation,
                ((self.env.runtime_name_for(storage.name) or storage.name)
                 if self.env.lookup_constructor(storage.name) is not None else None),
            )
            if base_operation in {"finish", "finish?"}:
                from ..contracts.release_effects import release_effects
                candidate = replace(candidate, branch=candidate.branch.with_element_tags(
                    release_effects(self.env, builder)))
        elif overload.reflection_operation is not None and overload.reflection_operation.startswith("dynamic:"):
            if not self.reflection_unsafe:
                self._diagnose(f"{node.name} requires lexical unsafe", node)
                return None
        elif overload.reflection_operation is not None:
            from valiance.asts import IndexAccessNode, IndexSelector, IndexSetNode
            from ..expressions.access_handlers import _index_access_node, _index_set_node

            arguments = candidate.source_arguments
            if candidate.applied.vectorised:
                arguments = candidate.applied.params
            writing = overload.reflection_operation == "write?"
            # Check the intrinsic through the same scalar indexing analyser.
            # Selector expressions were already evaluated as ordinary call arguments.
            selectors = (IndexSelector(start=(node,)),)
            access_node = (IndexSetNode(selectors, location=node.location) if writing else
                           IndexAccessNode(selectors, location=node.location))
            stack = (arguments[2], arguments[0], arguments[1]) if writing else arguments
            checked = (_index_set_node if writing else _index_access_node)(
                self, access_node, replace(candidate.branch, stack=T.TypeStack(stack), typed_body=()),
            )
            if not checked:
                return None
            checked_branch = checked.branches[0]
            reflection_access = replace(checked_branch.typed_body[-1], node=node)
            error = T.N(Symbol("ReflectionWriteError" if writing else "ReflectionReadError"))
            scalar_result = T.N(Symbol("Result"), reflection_access.typ, error)
            result = scalar_result
            if candidate.applied.vectorised:
                from valiance.vtypes.relations import _project_vector_shape
                result = _project_vector_shape(candidate.applied.actual_returns[0], result)
            from ..contracts.release_effects import release_effects
            effects = checked_branch.element_tags | release_effects(self.env, arguments[0])
            candidate = replace(candidate, branch=candidate.branch.with_element_tags(effects),
                                applied=replace(candidate.applied, returns=(scalar_result,), actual_returns=(result,)))

        actual_returns = annotation_hooks.annotated_element_returns(
            node,
            candidate.applied.actual_returns,
        )
        extension = self._analyse_element_extension(
            node.extension,
            candidate.applied,
            candidate.branch,
        )
        if node.extension is not None and extension is None:
            return None
        overload_runtime_name = self.env.overload_runtime_name_for(
            node.name, overload
        )
        behaviour_matches = tuple(
            (behaviour.provider, definition)
            for behaviour in self.env.context.trait_impl_behaviours
            if (definition := behaviour.definition(node.name)) is not None
        )
        behaviour_provider, behaviour_definition = (
            behaviour_matches[0] if len(behaviour_matches) == 1 else (None, None)
        )
        structural_witnesses = []
        callable_witnesses = []
        for index, (actual, param) in enumerate(zip(
            candidate.source_arguments, candidate.applied.params,
        )):
            source_type, expected_type = T.normalize(actual), T.normalize(param)
            if (isinstance(source_type, T.FunctionType)
                    and isinstance(expected_type, T.FunctionType)):
                for callback_index, (actual_input, expected_input) in enumerate(zip(
                    source_type.params, expected_type.params,
                )):
                    if not (isinstance(T.normalize(actual_input), T.AnonymousTraitType)
                            and isinstance(T.normalize(expected_input), T.NominalType)):
                        continue
                    try:
                        callback_proof = T.checked_structural_witnesses(
                            expected_input, T.normalize(actual_input), self.env.context,
                        )
                    except T.AmbiguousStructuralWitness as exc:
                        self._diagnose(str(exc), node)
                        return None
                    if callback_proof is not None:
                        callable_witnesses.extend(
                            (index, len(candidate.source_arguments), callback_index,
                             name, contract, identity)
                            for name, contract, identity in callback_proof
                        )
            if not isinstance(expected_type, T.AnonymousTraitType):
                continue
            try:
                proof = T.checked_structural_witnesses(
                    actual, T.normalize(param), self.env.context,
                )
            except T.AmbiguousStructuralWitness as exc:
                self._diagnose(str(exc), node)
                return None
            if proof is not None:
                structural_witnesses.extend(
                    (index, len(candidate.source_arguments), name, contract, identity)
                    for name, contract, identity in proof
                )
        return candidate.branch.push(*actual_returns).emit(
            TypedElementNode(
                node,
                _calls._returns_result_type(actual_returns),
                candidate.applied,
                candidate.implementation_id or overload.implementation_id,
                _calls._specialize_modifier_arguments(
                    candidate.applied,
                    candidate.modifiers,
                    self.env.context,
                ),
                candidate.call_arg_order,
                candidate.callable_implementation_id,
                extension,
                overload_runtime_name or self.env.runtime_name_for(node.name),
                behaviour_definition,
                behaviour_provider,
                structural_witnesses=tuple(structural_witnesses),
                callable_witnesses=tuple(callable_witnesses),
                specialization=candidate.specialization,
                reflection_access=reflection_access,
            )
        )

    def _call_element_call(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
    ) -> BranchSet:
        """Compute an explicit call of a callable value from the stack."""
        if node.modifier_args:
            self._diagnose("element 'call' does not accept ':' arguments", node)
            return BranchSet()
        if any(arg.name is not None for arg in node.call_args):
            self._diagnose(
                "named arguments are not supported when calling a function value; "
                "function values use positional arguments, so remove the argument "
                "names. Named arguments are available when calling a named element "
                "directly",
                node,
            )
            return BranchSet()
        stack_function_overloads = (
            _functions._callable_overloads(branch.stack.items[-1])
            if branch.stack
            else ()
        )
        if not stack_function_overloads:
            current = BranchSet((branch,))
            for arg in node.call_args:
                current = self.analyse_scoped_block(current, arg.value)
                if not current:
                    return BranchSet()
            terminal, current = _utils._split_terminal_branches(current)
            if not current:
                return terminal
            positional_candidates: list[CallCandidate] = []
            call_arg_count = len(node.call_args)
            for arg_branch in current:
                positional_candidates.extend(
                    self.call_element_candidates_for_branch(
                        node, overloads[0], arg_branch, call_arg_count
                    )
                )
            winners = self.select_call_winners(
                candidates=positional_candidates,
                branch=branch,
                node=node,
                no_match_message=(
                    "no overloads for element 'call' match explicit call syntax"
                ),
                ambiguous_message=(
                    "ambiguous overloads for element 'call' with explicit call syntax"
                ),
            )
            if winners is None:
                return terminal
            results: list[AnalysisBranch] = list(terminal.branches)
            for candidate in winners:
                evidence = self._higher_order_trait_evidence(node, candidate)
                if evidence is None:
                    continue
                results.append(
                    candidate.branch.push(*candidate.applied.actual_returns).emit(
                        TypedElementNode(
                            node,
                            _calls._returns_result_type(
                                candidate.applied.actual_returns
                            ),
                            candidate.applied,
                            candidate.applied.overload.implementation_id,
                            (),
                            candidate.call_arg_order,
                            candidate.callable_implementation_id,
                            structural_witnesses=evidence,
                            specialization=candidate.specialization,
                        )
                    )
                )
            return BranchSet.collect(results)

        function_type = branch.stack.items[-1]
        base = branch.with_stack(T.TypeStack(branch.stack.items[:-1]))
        candidates: list[CallCandidate] = []
        terminal: list[AnalysisBranch] = []

        for callable_overload in _functions._callable_overloads(function_type):
            prepared = _calls._prepare_element_call_branches(
                base,
                callable_overload,
                node.call_args,
                False,
                self,
                align_positional_right=False,
            )
            for preparation in prepared:
                if preparation.branch.terminal:
                    terminal.append(preparation.branch)
                    continue
                for args, popped, _modifiers in _calls._source_element_arguments(
                    preparation.branch,
                    callable_overload,
                    (),
                    self.env.context,
                    preparation.call_arg_order,
                    analyser=self,
                ):
                    stack_count = len(callable_overload.params) - sum(
                        not arg.placeholder for arg in node.call_args
                    )
                    argument_order = preparation.call_arg_order or tuple(
                        range(len(callable_overload.params))
                    )
                    runtime_order = tuple(
                        index if index < stack_count else index + 1
                        for index in argument_order
                    ) + (stack_count,)
                    identity = tuple(range(len(runtime_order)))
                    planned = _calls._call_element_candidates(
                        popped,
                        overloads[0],
                        function_type,
                        args,
                        popped.stack.items,
                        () if runtime_order == identity else runtime_order,
                        node.disambiguation,
                        self.env.context,
                        self.env,
                        self,
                    )
                    candidates.extend(
                        candidate
                        for candidate in planned
                        if candidate.callable_implementation_id == callable_overload.implementation_id
                    )

        winners = self.select_call_winners(
            candidates=candidates,
            branch=branch,
            node=node,
            no_match_message=(
                "no overloads for element 'call' match explicit call syntax"
            ),
            ambiguous_message=(
                "ambiguous overloads for element 'call' with explicit call syntax"
            ),
        )
        if winners is None:
            return BranchSet.collect(terminal)

        results: list[AnalysisBranch] = list(terminal)
        for candidate in winners:
            evidence = self._higher_order_trait_evidence(node, candidate)
            if evidence is None:
                continue
            extension = self._analyse_element_extension(
                node.extension,
                candidate.applied,
                candidate.branch,
            )
            if node.extension is not None and extension is None:
                continue
            results.append(
                candidate.branch.push(*candidate.applied.actual_returns).emit(
                    TypedElementNode(
                        node,
                        _calls._returns_result_type(candidate.applied.actual_returns),
                        candidate.applied,
                        candidate.applied.overload.implementation_id,
                        (),
                        candidate.call_arg_order,
                        candidate.callable_implementation_id,
                        extension,
                        structural_witnesses=evidence,
                        specialization=candidate.specialization,
                    )
                )
            )
        return BranchSet.collect(results)

    def _higher_order_trait_evidence(
        self, node: ElementNode, candidate: CallCandidate,
    ) -> tuple[tuple[int, int, str, tuple[tuple[str, ...], tuple[str, ...]], str], ...] | None:
        """Prove structural arguments to a statically typed callable value.

        `call`'s intrinsic signature is `(Function)`, but the selected
        callable has already been specialised by overload analysis.  Preserve
        its concrete argument requirements rather than dropping those proofs
        at the intrinsic boundary.
        """
        params = candidate.applied.params
        if not params:
            return ()
        callable_type = T.normalize(params[-1])
        if not isinstance(callable_type, T.FunctionType) or callable_type.params is None:
            return ()
        arity = len(params)
        evidence = []
        for index, (actual, required) in enumerate(zip(
            params[:-1], callable_type.params, strict=True,
        )):
            required = T.normalize(required)
            if not isinstance(required, T.AnonymousTraitType):
                continue
            try:
                proof = T.checked_structural_witnesses(
                    actual, required, self.env.context,
                )
            except T.AmbiguousStructuralWitness as exc:
                self._diagnose(str(exc), node)
                return None
            if proof:
                evidence.extend(
                    (index, arity, name, contract, identity)
                    for name, contract, identity in proof
                )
        return tuple(evidence)

    def call_element_candidates_for_branch(
        self,
        node: ElementNode,
        call_overload: T.Overload,
        arg_branch: AnalysisBranch,
        call_arg_count: int,
    ) -> list[CallCandidate]:
        """Build callable-value candidates for the built-in `call` element."""
        if len(arg_branch.stack) < call_arg_count:
            return []

        call_values = arg_branch.stack.items[-call_arg_count:] if call_arg_count else ()
        base_stack = arg_branch.stack.items[:-call_arg_count]
        explicit_function_order = (
            (*range(1, call_arg_count), 0) if call_arg_count > 1 else ()
        )
        candidates = _calls._call_element_candidates(
            arg_branch,
            call_overload,
            call_values[0],
            call_values[1:],
            base_stack,
            explicit_function_order,
            node.disambiguation,
            self.env.context,
            self.env,
            self,
        )
        if candidates or not base_stack:
            return candidates

        return _calls._call_element_candidates(
            arg_branch,
            call_overload,
            base_stack[-1],
            call_values,
            base_stack[:-1],
            (),
            node.disambiguation,
            self.env.context,
            self.env,
            self,
        )

