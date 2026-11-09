"""Element invocation and smart call diagnostics."""

from __future__ import annotations

from dataclasses import field, replace

import valiance.analysis.contracts.annotations as annotation_hooks
import valiance.vtypes as T
from valiance.vtypes.relations import _contains_type_var
from valiance.vtypes.relations import implementation_target_providers
from valiance.asts import ElementNode
from valiance.vtypes.symbols import Symbol

from ..calls import candidates as _calls
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import AnalysisBranch, BranchSet
from .models import ModifierArgumentAnalysis


class _ElementCalls:
    """Own element invocation and suggestion operations."""

    def _element(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
        *,
        _allow_no_match: bool = False,
    ) -> BranchSet:
        """Analyse a `ElementNode` node and return the surviving branches."""
        from valiance.asts import TypeArgumentExpression

        if any(isinstance(argument, TypeArgumentExpression) for argument in node.generic_args):
            return self._descriptor_conversion(node, branch)
        optional_conversion = node.name.text == "to?"
        if optional_conversion:
            node = replace(node, name=Symbol("to", node.name.namespace))
        overloads = self.env.overloads_for(node.name)
        if optional_conversion:
            overloads = tuple(overload for overload in overloads
                              if overload.conversion_target is not None)
        overloads = self._trait_receiver_overloads(node.name, branch, overloads)
        if node.name == Symbol("dup") and branch.stack:
            decision = _utils._duplication_requirement(branch.stack[-1], self.env)
            if decision.reason is not None:
                self._diagnose(decision.reason, node)
                return BranchSet()
        if not overloads:
            self._diagnose(self._unknown_element_message(node, branch), node)
            return BranchSet()
        if not annotation_hooks.valid_element_annotations(node.annotations):
            self._diagnose(
                f"unsupported element annotation on '{node.name}'",
                node,
            )
            return BranchSet()

        modifier_args = self._modifier_argument_types(branch, node)
        if modifier_args is None:
            return BranchSet()
        if node.modifier_args and not _calls._modifier_arity_matches(
            overloads,
            modifier_args,
        ):
            self._diagnose(
                f"element '{node.name}' expects "
                f"{_calls._show_modifier_counts(overloads)} ':' function argument(s), "
                f"got {len(modifier_args)}",
                node,
            )
            return BranchSet()

        stack_callable = T.normalize(branch.stack[-1]) if branch.stack else None
        generic_overload_set = (
            isinstance(stack_callable, T.OverloadSetType)
            and any(
                _contains_type_var(item)
                for overload in stack_callable.overloads
                for item in (*overload.params, *overload.returns)
            )
        )
        if node.name == Symbol("call") and (
            node.call_args or (node.explicit_call and generic_overload_set)
        ):
            return self._call_element_call(branch, node, overloads)

        diagnostics_before = len(self.diagnostics)
        sources, terminal = self.element_argument_sources(
            node,
            branch,
            overloads,
            modifier_args,
        )
        if not sources and terminal:
            return BranchSet.collect(terminal)
        if not sources and len(self.diagnostics) > diagnostics_before:
            return BranchSet()
        failure_start = len(self.call_site_failures)
        candidates = self.element_call_candidates(node, overloads, sources)
        body_failures = tuple(dict.fromkeys(self.call_site_failures[failure_start:]))
        del self.call_site_failures[failure_start:]
        if _allow_no_match and not candidates:
            return BranchSet.collect(terminal)
        if node.name in (Symbol("field?"), Symbol("length")) and not self.reflection_unsafe:
            from valiance.vtypes.reflection import fields_type_owners
            permitted = []
            denied = []
            for candidate in candidates:
                protected = next((owner for param in candidate.applied.params
                                  for owner in (fields_type_owners(param) or ())
                                  if self.env.reflection_requires_unsafe(owner)), None)
                if protected is None:
                    permitted.append(candidate)
                else:
                    denied.append(protected)
            if not permitted and denied:
                self.require_reflection_permission(denied[0], node)
                return BranchSet()
            candidates = permitted

        stack_before = branch.stack

        def no_match_message() -> str:
            """Build detailed overload diagnostics only after selection fails."""
            modifier_signature = self._modifier_signature_message(modifier_args)
            modifier_mismatch = self._modifier_generic_mismatch_message(
                node, stack_before, overloads, modifier_args
            )
            overload_list = _utils._show_overload_list(node.name, overloads)
            if body_failures:
                overload_list += "\nrejected function bodies:\n" + "\n".join(body_failures)
            if node.call_args:
                call_shape = self._explicit_call_shape_message(node, overloads)
                if call_shape is not None:
                    return (
                        f"{call_shape}\n{modifier_signature}"
                        f"{modifier_mismatch}{overload_list}"
                    )
                return (
                    f"no overloads for element '{node.name}' match explicit call "
                    f"syntax\n{modifier_signature}{modifier_mismatch}{overload_list}"
                )
            message = (
                f"no overloads for element '{node.name}' match stack "
                f"{_utils._show_stack(stack_before)}\n"
                f"{modifier_signature}{modifier_mismatch}{overload_list}"
            )
            if not candidates:
                near_miss_help = self._element_near_miss_help(node, branch)
                if near_miss_help is not None:
                    message += f"\nhelp: {near_miss_help}"
            return message

        def ambiguous_message() -> str:
            """Render the source stack only for genuinely ambiguous overloads."""
            if node.call_args:
                return (
                    f"ambiguous overloads for element '{node.name}' "
                    "with explicit call syntax"
                )
            return (
                f"ambiguous overloads for element '{node.name}' with stack "
                f"{_utils._show_stack(stack_before)}"
            )
        winners = self.select_call_winners(
            candidates=candidates,
            branch=branch,
            node=node,
            no_match_message=no_match_message,
            ambiguous_message=ambiguous_message,
        )
        if winners is None:
            return BranchSet.collect(terminal)

        results: list[AnalysisBranch] = list(terminal)
        for candidate in winners:
            # Native State retains the initial value independently for immutable
            # snapshots and future callback execution.  Reject concrete types
            # that cannot be duplicated or moved between tasks at the call site;
            # unresolved generic inputs remain subject to the runtime checks.
            if (candidate.implementation_id or "").startswith(
                "native:std.concurrency.State:"
            ):
                from ..transfer import (
                    shared_value_duplication_violation,
                    validate_task_transfer_type,
                )

                initial_type = candidate.applied.params[0]
                duplication = shared_value_duplication_violation(
                    initial_type, self.env, path="State initial value"
                )
                if duplication is not None:
                    self._diagnose(
                        "State initial value must be duplicable: " + duplication,
                        node,
                    )
                    continue
                violation = validate_task_transfer_type(
                    initial_type, self.env, path="State initial value"
                )
                if violation is not None:
                    self._diagnose(
                        "State initial value must be task-transferable: "
                        + violation.render(), node,
                    )
                    continue
            if optional_conversion:
                candidate = replace(candidate, applied=replace(
                    candidate.applied,
                    element_tags=frozenset(tag for tag in candidate.applied.element_tags
                                           if tag.name != Symbol("Panic")),
                ))
            committed = self.commit_element_candidate(node, overloads, candidate)
            if committed is not None:
                if optional_conversion:
                    result_type = T.optional(committed.stack[-1])
                    typed = replace(committed.typed_body[-1],
                                    typ=result_type, optional_conversion=True)
                    committed = replace(
                        committed.pop().push(result_type),
                        typed_body=(*committed.typed_body[:-1], typed),
                    )
                results.append(committed)
        return BranchSet.collect(results)

    def _descriptor_conversion(self, node: ElementNode, branch: AnalysisBranch) -> BranchSet:
        """Check ordinary conversion calls for each visible runtime descriptor target."""
        from valiance.asts import CallArgument, TypeArgumentExpression, TypedDescriptorConversionNode
        from .models import ReflectionSpecializationRequired

        if len(node.generic_args) != 1 or not isinstance(node.generic_args[0], TypeArgumentExpression):
            self._diagnose("a descriptor-directed conversion requires one Type expression", node)
            return BranchSet()
        optional = node.name.text == "to?"
        name = Symbol("to", node.name.namespace) if optional else node.name
        overloads = tuple(item for item in self.env.overloads_for(name) if item.conversion_target is not None)
        if not overloads:
            self._diagnose("runtime Type arguments require a conversion provider", node)
            return BranchSet()
        implicit = (not node.call_args and not node.modifier_args) or any(
            argument.placeholder for argument in node.call_args
        )
        source_branch = branch
        source_type = None
        if implicit:
            sourced = branch.source_arguments((T.V("ConversionSource"),))
            if sourced is None:
                self._diagnose("descriptor-directed conversion requires a source value", node)
                return BranchSet()
            (source_type,), source_branch = sourced
        seed = replace(source_branch, stack=T.TypeStack())
        targets = self.analyse_scoped_block(BranchSet((seed,)), node.generic_args[0].body)
        results = []
        for evidence_branch in targets:
            if evidence_branch.terminal:
                target_body = evidence_branch.typed_body[len(branch.typed_body):]
                results.append(replace(evidence_branch, typed_body=branch.typed_body).emit(
                    TypedDescriptorConversionNode(node, T.Never(), target_body, (), (), optional, implicit),
                ))
                continue
            if len(evidence_branch.stack) != 1:
                self._diagnose("conversion target expression must produce one Type value", node)
                continue

            def bounds(typ):
                """Recover represented types without erasing a descriptor union."""
                typ = T.normalize(typ)
                if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
                    return bounds(typ.inner)
                if isinstance(typ, T.RowType):
                    return bounds(typ.base)
                if isinstance(typ, T.UnionType):
                    values = tuple(bounds(item) for item in typ.items)
                    return T.U(*values) if all(value is not None for value in values) else None
                if isinstance(typ, T.NominalType) and typ.name == Symbol("Type") and len(typ.args) == 1:
                    return typ.args[0]
                return None

            bound = bounds(evidence_branch.stack[-1])
            if bound is None:
                self._diagnose("conversion target expression must have Type[T] evidence", node)
                continue
            unresolved = next((typ for typ in (bound, *branch.stack, *source_branch.inputs)
                               if _contains_type_var(typ)), None)
            if unresolved is not None:
                if self.defer_reflection:
                    raise ReflectionSpecializationRequired(unresolved)
                self._diagnose("descriptor-directed conversion requires concrete type evidence", node)
                continue
            baseline = replace(evidence_branch, stack=(source_branch.stack.push(source_type)
                                                       if implicit else branch.stack))
            start = len(baseline.typed_body)
            choices = []
            joined = None
            possible = sorted({T.normalize(item.conversion_target) for item in overloads
                               if not _contains_type_var(item.conversion_target)
                               and T.assignable(item.conversion_target, bound, self.env.context)}, key=T.show)
            diagnostics = len(self.diagnostics)
            for target in possible:
                outputs = self._element(replace(node, generic_args=(target,)), baseline, _allow_no_match=True)
                if len(self.diagnostics) != diagnostics:
                    return BranchSet()
                if not outputs:
                    continue
                body = _patterns._typed_block(outputs, start, ())
                if not body:
                    self._diagnose("conversion requires a unique checked body for each runtime target", node)
                    return BranchSet()
                choices.append((target, body))
                for output in outputs:
                    joined = _patterns._join_match_output(original=baseline, baseline=baseline,
                                                          joined=joined, candidate=output, ctx=self.env.context)
                    if joined is None:
                        self._diagnose("runtime conversion branches infer incompatible inputs", node)
                        return BranchSet()
            if joined is None:
                self._diagnose(f"no visible conversion matches Type[{T.show(bound)}] and the supplied source", node)
                continue
            call_args = tuple(replace(argument, name=None) for argument in node.call_args)
            if node.modifier_args:
                call_args += tuple(CallArgument(value=(function,)) for function in node.modifier_args)
            fallback = self._element(ElementNode(Symbol("*::pop"), call_args=call_args,
                                                  explicit_call=node.explicit_call or bool(call_args)), baseline)
            if not fallback:
                return BranchSet()
            checked_fallback = _patterns._typed_block(fallback, start, ())
            if not checked_fallback:
                self._diagnose("conversion fallback requires consistent source evaluation", node)
                return BranchSet()
            fallback_body = checked_fallback[:-1]
            for output in fallback:
                output = output.push(T.NoneType() if optional else T.Never())
                if not optional:
                    output = output.with_element_tags((T.ElementTag(Symbol("Panic"), (T.N(Symbol("ConversionFault")),)),))
                joined = _patterns._join_match_output(original=baseline, baseline=baseline,
                                                      joined=joined, candidate=output, ctx=self.env.context)
                if joined is None:
                    self._diagnose("runtime conversion fallback infers incompatible inputs", node)
                    return BranchSet()
            target_body = evidence_branch.typed_body[len(branch.typed_body):]
            result = replace(joined, typed_body=branch.typed_body).emit(TypedDescriptorConversionNode(
                node, joined.stack[-1], target_body, tuple(choices), fallback_body, optional, implicit,
            ))
            results.append(result)
        return BranchSet.collect(results)

    @staticmethod
    def _modifier_signature_message(
        modifier_args: tuple[ModifierArgumentAnalysis, ...],
    ) -> str:
        """Render the inferred signatures supplied after ``:`` in diagnostics."""
        if not modifier_args:
            return ""
        heading = (
            "modifier argument signature:"
            if len(modifier_args) == 1
            else "modifier argument signatures:"
        )
        signatures = "\n".join(
            f"  - {index}: {T.show(argument.typ)}"
            for index, argument in enumerate(modifier_args, start=1)
        )
        return f"{heading}\n{signatures}\n"

    @staticmethod
    def _modifier_generic_mismatch_message(
        node: ElementNode,
        stack: T.TypeStack,
        overloads: tuple[T.Overload, ...],
        modifier_args: tuple[ModifierArgumentAnalysis, ...],
    ) -> str:
        """Explain when a collection's generic item type cannot be specialized."""
        if len(modifier_args) != 1 or not stack:
            return ""
        actual = T.normalize(stack[-1])
        modifier = T.normalize(modifier_args[0].typ)
        if not isinstance(actual, T.CollectionType):
            return ""
        if not isinstance(T.normalize(actual.base), T.VarType):
            return ""
        modifier_options = (
            (modifier,)
            if isinstance(modifier, T.FunctionType)
            else tuple(
                T.normalize(T.Fn(overload.params, overload.returns))
                for overload in modifier.overloads
            )
            if isinstance(modifier, T.OverloadSetType)
            else ()
        )
        concrete_options = tuple(
            option
            for option in modifier_options
            if isinstance(option, T.FunctionType)
            and option.params
            and not any(
                isinstance(T.normalize(param), T.VarType)
                for param in option.params
            )
        )
        if not concrete_options:
            return ""
        arities = {len(option.params or ()) for option in concrete_options}
        if len(arities) != 1:
            return ""
        # Prefer a signature whose inputs accept all the more specific
        # overloads. This describes the common input contract without
        # depending on overload declaration order or particular type names.
        widest_options = tuple(
            option
            for option in concrete_options
            if all(
                all(
                    T.assignable(other_param, param, T.Context())
                    for param, other_param in zip(
                        option.params or (), other.params or (), strict=True
                    )
                )
                for other in concrete_options
            )
        )
        modifier = widest_options[0] if widest_options else concrete_options[0]
        reducer = next(
            (
                overload
                for overload in overloads
                if any(
                    isinstance(T.normalize(param), T.FunctionType)
                    for param in overload.params
                )
                and any(
                    isinstance(T.normalize(param), T.CollectionType)
                    for param in overload.params
                )
            ),
            None,
        )
        if reducer is None:
            return ""
        signature = _utils._show_overload_signature(node.name, reducer)
        item_type = T.show(actual.base)
        modifier_inputs = ", ".join(T.show(param) for param in modifier.params)
        return (
            "closest modifier overload mismatch:\n"
            f"  - {signature}\n"
            f"  - collection item type: {item_type} (generic type variable)\n"
            f"  - ':' function inputs: {modifier_inputs}\n"
            f"help: The preceding expression leaves `{item_type}` as an unresolved "
            "generic type variable, so it cannot be matched with the reducer's "
            f"`{modifier_inputs}` inputs. Check the stack-producing operation "
            "immediately before this call.\n"
        )

    def _trait_receiver_overloads(
        self,
        name: Symbol,
        branch: AnalysisBranch,
        overloads: tuple[T.Overload, ...],
    ) -> tuple[T.Overload, ...]:
        """Add scalar trait contracts before ordinary vectorisation matching."""
        if not branch.stack:
            if not branch.cycle_params:
                return overloads
            sourced = branch.source_arguments(branch.cycle_params[:1])
            if sourced is None:
                return overloads
            receiver = T.normalize(sourced[0][-1])
        else:
            receiver = T.normalize(branch.stack[-1])
        while isinstance(receiver, (T.CollectionType, T.TaggedType, T.ExactType)):
            receiver = T.normalize(
                receiver.base if isinstance(receiver, T.CollectionType) else receiver.inner
            )
        receivers = (
            receiver.items
            if isinstance(receiver, T.IntersectionType)
            else (receiver,)
        )
        specialized = list(overloads)
        for trait_receiver in receivers:
            trait_receiver = T.normalize(trait_receiver)
            if not isinstance(trait_receiver, T.NominalType):
                continue
            trait = self.env.lookup_trait(trait_receiver.name)
            if (trait is None or not trait.requirements) and trait_receiver.name.namespace:
                trait = self.env.lookup_trait(Symbol(trait_receiver.name.text))
            if trait is None:
                continue
            substitution = {
                generic.text: argument
                for generic, argument in zip(
                    trait.generics,
                    trait_receiver.args,
                    strict=False,
                )
            }
            substitution["$self"] = trait_receiver
            for requirement in trait.requirements:
                if requirement.name != name:
                    continue
                contract = replace(
                    requirement.overload,
                    is_multi=True,
                    params=(
                        *(
                            T._substitute(param, substitution)
                            for param in requirement.overload.params
                        ),
                        trait_receiver,
                    ),
                    returns=tuple(
                        T._substitute(result, substitution)
                        for result in requirement.overload.returns
                    ),
                    param_names=(*requirement.overload.param_names, None),
                )
                defaults = self.env.overloads_for(Symbol(f"{trait.name}::{name}"))
                default_ids = {overload.implementation_id for overload in defaults}
                specialized = [overload for overload in specialized
                               if not (overload.implementation_id in default_ids
                                       and overload.params == contract.params)]
                provider_name = (
                    Symbol(trait_receiver.name.namespace[-1])
                    if trait_receiver.name.namespace
                    else None
                )
                implementations = tuple(
                    behaviour
                    for behaviour in self.env.context.trait_impl_behaviours
                    if behaviour.definition(name) is not None
                    and isinstance(
                        T.normalize(behaviour.trait_pattern), T.NominalType
                    )
                    and T.normalize(behaviour.trait_pattern).name.text
                    == trait_receiver.name.text
                )
                if provider_name is None and implementations:
                    applicable = implementation_target_providers(trait_receiver, self.env.context)
                    if len(applicable) == 1:
                        provider_name = next(iter(applicable))
                if provider_name is not None and implementations:
                    provider_trait = (trait_receiver.name if trait_receiver.name.namespace
                                      else Symbol(trait_receiver.name.text, (provider_name.text,)))
                    qualified = self.env.overloads_for(Symbol(f"{provider_trait}::{name}"))
                    if not qualified:
                        qualified = self.env.overloads_for(Symbol(
                            name.text, (*provider_trait.namespace, provider_trait.text)))
                    for original in qualified:
                        linked = replace(contract, implementation_id=original.implementation_id,
                                         is_multi=original.is_multi)
                        runtime_name = self.env.overload_runtime_name_for(
                            Symbol(f"{provider_trait}::{name}"), original)
                        if runtime_name is not None:
                            self.env.bind_runtime_name(name, runtime_name, linked)
                        specialized.append(linked)
                    if not qualified:
                        specialized.append(contract)
                else:
                    specialized.append(contract)
        return tuple(specialized)

    def _unknown_element_message(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> str:
        """Build an unknown-element message with type-viable typo suggestions."""
        message = f"unknown element '{node.name}'"
        failed_import = next(
            (
                module
                for namespace, module in self._failed_imports.items()
                if node.name.namespace[: len(namespace)] == namespace
            ),
            None,
        )
        if failed_import is not None:
            return (
                f"{message}\n"
                f"note: '{node.name}' is unavailable because module "
                f"'{failed_import}' could not be imported\n"
                "help: fix the import above before checking this element name"
            )
        if node.name.namespace:
            namespace = Symbol(node.name.namespace[0])
            exports = self._imported_namespace_exports.get(namespace)
            if exports is not None:
                private_object = any(
                    obj.name == Symbol(node.name.text) and not obj.public
                    for obj in exports.objects
                )
                private_definition = any(
                    definition.name == Symbol(node.name.text) and not definition.public
                    for definition in exports.definitions
                )
                if private_object or private_definition:
                    module = self._imported_namespace_sources.get(namespace, str(namespace))
                    return (
                        f"{message}\n"
                        f"note: '{node.name.text}' is declared in module '{module}' "
                        "but is not public\n"
                        f"help: mark '{node.name.text}' as public to export it from "
                        f"module '{module}'"
                    )
        private_friendly = self._private_friendly_element_message(
            node.name,
            branch,
        )
        if private_friendly is not None:
            return f"{message}\n{private_friendly}"
        if branch.variables.read(node.name) is not None:
            return f"{message}\ndid you mean '${node.name}'?"
        suggestions = self._element_name_suggestions(node, branch)
        if not suggestions:
            return message
        return f"{message}\ndid you mean:\n" + "\n".join(
            f"  - {suggestion}" for suggestion in suggestions
        )

    def _private_friendly_element_message(
        self,
        name: Symbol,
        branch: AnalysisBranch,
    ) -> str | None:
        """Explain when an imported object's matching friendly element is private."""
        if not branch.stack:
            return None
        receiver = branch.stack[-1]
        candidates = tuple(
            candidate
            for candidate in self._private_imported_friendly_elements.get(name, ())
            if self._receiver_may_be_owner(receiver, candidate[0])
        )
        if not candidates:
            return None
        unique = tuple(dict.fromkeys(candidates))
        if len(unique) == 1:
            owner, module = unique[0]
            return (
                f"note: object-friendly element '{name.text}' is declared for "
                f"object '{owner}' in module '{module}' but is not public\n"
                f"help: mark '{name.text}' as public to make it available when "
                f"importing '{owner}'"
            )
        rendered = "\n".join(
            f"  - {module}.{owner}::{name.text}"
            for owner, module in sorted(unique, key=lambda item: (item[1], str(item[0])))
        )
        return (
            "note: matching private object-friendly elements were found:\n"
            f"{rendered}\n"
            "help: mark the intended definition as public, or use a different "
            "public element"
        )

    def _receiver_may_be_owner(self, receiver: T.Type, owner: Symbol) -> bool:
        """Return whether one runtime receiver branch can have the imported owner."""
        receiver = T.normalize(receiver)
        if isinstance(receiver, (T.TaggedType, T.NoVecType, T.ExactType)):
            return self._receiver_may_be_owner(receiver.inner, owner)
        if isinstance(receiver, T.UnionType):
            return any(
                self._receiver_may_be_owner(item, owner) for item in receiver.items
            )
        return T.assignable(receiver, T.N(owner), self.env.context)

    def _element_name_suggestions(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> tuple[str, ...]:
        """Return close visible element signatures that can consume this call."""
        attempted = str(node.name)
        ranked: list[tuple[float, Symbol]] = []
        for name in self.env.visible_overload_names():
            if _utils._internal_element_name(name):
                continue
            score = _utils._name_similarity(attempted, str(name))
            if score >= 0.62:
                ranked.append((score, name))
        ranked.sort(key=lambda item: (-item[0], str(item[1])))

        suggestions: list[str] = []
        for _, name in ranked[:12]:
            for overload in self._viable_suggestion_overloads(node, branch, name):
                rendered = _utils._show_overload_signature(name, overload)
                if rendered not in suggestions:
                    suggestions.append(rendered)
                if len(suggestions) == 3:
                    return tuple(suggestions)
        return tuple(suggestions)

    def _viable_suggestion_overloads(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
        name: Symbol,
    ) -> tuple[T.Overload, ...]:
        """Probe one similar name without leaking speculative diagnostics."""
        overloads = self.env.overloads_for(name)
        if not overloads:
            return ()
        candidate_node = replace(node, name=name, annotations=(), extension=None)
        probe = self._child_analyser(self.env.lexical_child_scope())
        prelude_nodes = len(self._prelude.nodes)
        prelude_bindings = len(self._prelude.bindings)
        try:
            modifiers = probe._modifier_argument_types(branch, candidate_node)
            if modifiers is None:
                return ()
            if candidate_node.modifier_args and not _calls._modifier_arity_matches(
                overloads,
                modifiers,
            ):
                return ()
            if candidate_node.call_args and name == Symbol("call"):
                return ()
            sources, _ = probe.element_argument_sources(
                candidate_node,
                branch,
                overloads,
                modifiers,
            )
            candidates = probe.element_call_candidates(
                candidate_node,
                overloads,
                sources,
            )
            viable: list[T.Overload] = []
            for candidate in candidates:
                overload = candidate.applied.overload
                if overload.annotation_error is not None or overload in viable:
                    continue
                viable.append(overload)
            return tuple(viable)
        finally:
            del self._prelude.nodes[prelude_nodes:]
            del self._prelude.bindings[prelude_bindings:]

    def _element_near_miss_help(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> str | None:
        """Return guidance when a failed element call matches a close semantic peer."""
        if (
            node.name != Symbol("fold")
            or node.call_args
            or len(node.modifier_args) != 1
        ):
            return None
        if not self._viable_suggestion_overloads(node, branch, Symbol("reduce")):
            return None
        return (
            "`fold` requires an explicit accumulator seed; add one before the call, "
            "for example `0 fold: +`, or use `reduce: +` to use the first item"
        )

    def _explicit_call_shape_message(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
    ) -> str | None:
        """Diagnose named-argument mistakes before generic overload failure."""
        named_args = tuple(arg.name for arg in node.call_args if arg.name is not None)
        seen: set[Symbol] = set()
        for name in named_args:
            if name in seen:
                return (
                    f"named argument '{name}' is provided more than once for "
                    f"element '{node.name}'"
                )
            seen.add(name)

        parameter_names = tuple(
            name
            for overload in overloads
            for name in overload.param_names
            if name is not None
        )
        known = set(parameter_names)
        for name in named_args:
            if name in known:
                continue
            message = f"unknown named argument '{name}' for element '{node.name}'"
            suggestions = _utils._similar_names(str(name), parameter_names, limit=1)
            if suggestions:
                message += f"\ndid you mean '{suggestions[0]}'?"
            return message
        return None
