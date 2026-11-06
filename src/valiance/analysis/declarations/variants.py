"""Focused variants declaration analysis."""

from __future__ import annotations

from ..overload_identity import declaration_id, declaration_prefix

from dataclasses import replace

import valiance.analysis.contracts.annotations as annotation_hooks
import valiance.vtypes as T
from valiance.asts import DefineNode, FunctionNode, FunctionParam, ObjectNode, TypedFunctionNode, TypedNode
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.structural import nested_types

from ..calls import callable_values as _functions
from ..support import analysis_utils as _utils
from ..state import AnalysisBranch, BranchSet
class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

class _VariantDeclarations:
    """Own variants declaration operations."""

    def _define_variant_shape(
        self, node: ObjectNode, *, member_aliases: bool = False,
    ) -> tuple[Symbol, ...] | None:
        """Register closed member storage and parent relations without checking bodies."""
        generic_constraints = _functions._generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        requirements = _utils._trait_requirements(node)
        members: list[Symbol] = []
        # Variant case payloads are immutable values. Each payload contributes
        # a covariant read of its type parameters, unlike a writable object field.
        member_payloads: list[T.ObjectAttribute] = []
        for member in node.variants:
            member_name = _utils._child_symbol(node.name, member.name)
            members.append(member_name)
            object_attributes = self._object_attributes(member.fields, node.generics)
            if object_attributes is None:
                return None
            member_payloads.extend(object_attributes)
            variant_type = _utils._declared_nominal(node.name, node.generics)
            self._define_object_shape(
                member_name,
                node,
                object_attributes,
                result_type=variant_type,
                generic_constraints=generic_constraints,
            )
            if member_aliases:
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
                readonly_attributes=tuple(member_payloads),
            ),
            requirements=requirements,
        )
        if annotation_hooks.has_annotation(node.annotations, "errType"):
            self.env.add_trait_impl(node.name, Symbol("Err"))
        return tuple(members)

    def _variant_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build the definition for variant during static analysis."""
        members = self._define_variant_shape(node, member_aliases=True)
        if members is None:
            return BranchSet((branch.emit(TypedNode(node, None)),))
        requirements = _utils._trait_requirements(node)

        current = branch.emit(
            TypedNode(node, _utils._declared_nominal(node.name, node.generics))
        )
        requirements_by_name = {
            requirement.name: requirement for requirement in requirements
        }
        default_names = {definition.name for definition in node.definitions}
        for member, member_name in zip(node.variants, members, strict=True):
            definitions_by_name: dict[Symbol, list[DefineNode]] = {}
            for definition in member.definitions:
                definitions_by_name.setdefault(definition.name, []).append(definition)

            for requirement in requirements:
                implementations = definitions_by_name.get(requirement.name, ())
                if not implementations and requirement.name not in default_names:
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

            # Abstract member implementations are visible when checking defaults,
            # so a shared body can call an `extend` through its variant receiver.
            for definition in member.definitions:
                if definition.name in requirements_by_name:
                    current = self._register_variant_member_definition(
                        current, node, member_name, definition,
                        requirements_by_name[definition.name],
                        expose_member_receiver=definition.name in default_names,
                    )

        # A variant-level definition is the ordinary, non-multi fallback.
        # Concrete member implementations may specialize it, but the default
        # must not compete with those specializations in runtime dispatch.
        for definition in node.definitions:
            current = self._register_friendly_definition(
                current, node.name, definition,
                requirements_by_name.get(definition.name), owner_node=node,
            )

        defaults_by_name: dict[Symbol, T.TraitRequirement] = {}
        for definition in node.definitions:
            name = Symbol(f"{node.name}::{definition.name}")
            candidates = self.env.overloads_for(name)
            prefix = (declaration_prefix(self.source_file, definition.location)
                      if definition.location is not None else "synthetic:")
            for overload in candidates:
                if not overload.implementation_id or not overload.implementation_id.startswith(prefix):
                    continue
                receiver = next(iter(definition.function.explicit_receiver_indices), 0)
                defaults_by_name[definition.name] = T.TraitRequirement(
                    definition.name,
                    replace(
                        overload,
                        params=tuple(param for index, param in enumerate(overload.params)
                                     if index != receiver),
                        param_names=tuple(param for index, param in enumerate(overload.param_names)
                                          if index != receiver),
                        implementation_id=None,
                    ),
                )

        for member, member_name in zip(node.variants, members, strict=True):
            for definition in member.definitions:
                if definition.name in requirements_by_name:
                    continue
                current = self._register_variant_member_definition(
                    current, node, member_name, definition,
                    defaults_by_name.get(definition.name),
                    expose_member_receiver=definition.name in defaults_by_name,
                )

        # A selected parent default must remain dynamically dispatched, even
        # when generic receiver variables use distinct analyser binder IDs.
        # The emitted default itself stays a non-multi fallback.
        for definition in node.definitions:
            name = definition.name
            qualified = Symbol(f"{node.name}::{name}")
            identities = {item.implementation_id for item in self.env.overloads_for(qualified)}
            if not identities:
                continue
            self.env.overloads[name] = [
                replace(overload, is_multi=True)
                if overload.implementation_id in identities else overload
                for overload in self.env.overloads_for(name)
            ]

        return BranchSet((self._attach_method_results(current, node),))

    def _register_variant_member_definition(
        self,
        branch: AnalysisBranch,
        variant: ObjectNode,
        owner: Symbol,
        definition: DefineNode,
        requirement: T.TraitRequirement | None,
        *,
        expose_member_receiver: bool = False,
    ) -> AnalysisBranch:
        """Register variant member definition during static analysis."""
        if requirement is None:
            return self._register_friendly_definition(branch, owner, definition)
        if expose_member_receiver and variant.generics:
            # Defaults were checked in their own generic scope. A case override
            # uses the variant's generics, not the default method's binder.
            generic_names = {generic.text for generic in variant.generics}

            def member_generic_type(typ: T.Type) -> T.Type:
                """Rebind the variant's type variables to the override scope."""
                substitutions = {
                    T.type_var_key(item): T.V(item.name)
                    for item in nested_types(typ)
                    if isinstance(item, T.VarType) and item.name in generic_names
                }
                return T._substitute(typ, substitutions)

            contract = requirement.overload
            requirement = replace(requirement, overload=replace(
                contract,
                params=tuple(member_generic_type(param) for param in contract.params),
                returns=tuple(member_generic_type(result) for result in contract.returns),
            ))
        if not self._validate_annotations(definition.annotations, "define", definition):
            return branch.emit(TypedNode(definition, None))

        receiver_indices = definition.function.explicit_receiver_indices
        if len(receiver_indices) > 1:
            self._diagnose("object-friendly elements may declare only one $self receiver", definition)
            return branch.emit(TypedNode(definition, None))
        explicit_params = tuple(
            param for index, param in enumerate(definition.function.params or ())
            if index not in receiver_indices
        )
        if len(explicit_params) != len(requirement.overload.params):
            self._diagnose(
                f"variant member element '{definition.name}' must take "
                f"{len(requirement.overload.params)} explicit parameter(s), got "
                f"{len(explicit_params)}",
                definition,
            )
            return branch.emit(TypedNode(definition, None))

        # The variant's generic binder can be specialized independently while
        # checking a shared default and a case override. Compare owner type
        # variables by their declared names, not those temporary binder IDs.
        owner_generic_names = {generic.text for generic in variant.generics}

        def same_signature_type(left: T.Type, right: T.Type) -> bool:
            """Compare member contracts modulo variant generic binder identity."""
            def normalize_owner_generics(typ: T.Type) -> T.Type:
                """Normalize owner generics to their declaration names."""
                if (isinstance(typ, T.NominalType) and not typ.args
                        and not typ.name.namespace and typ.name.text in owner_generic_names):
                    return T.V(typ.name.text)
                substitutions = {
                    T.type_var_key(item): T.V(item.name)
                    for item in nested_types(typ)
                    if isinstance(item, T.VarType) and item.name in owner_generic_names
                }
                return T._substitute(typ, substitutions)

            return T.same(
                normalize_owner_generics(left), normalize_owner_generics(right)
            )

        contextual_params: list[FunctionParam] = []
        for source, required in zip(
            explicit_params,
            requirement.overload.params,
            strict=True,
        ):
            if source.typ is not None and not same_signature_type(source.typ, required):
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
                not same_signature_type(actual, required)
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

        if receiver_indices:
            receiver_index = receiver_indices[0]
            contextual_params.insert(receiver_index, definition.function.params[receiver_index])
            contextual_definition = replace(definition, function=replace(
                definition.function, params=tuple(contextual_params),
                returns=requirement.overload.returns,
            ))
            current = self._register_friendly_definition(
                branch, owner, contextual_definition, owner_node=variant,
            )
            candidates = self.env.overloads_for(Symbol(f"{owner}::{definition.name}"))
            if not candidates:
                return current
            concrete = candidates[-1]
            if concrete.element_tags != requirement.overload.element_tags:
                self._diagnose(
                    f"variant member element '{definition.name}' element tags must "
                    "exactly match the variant extend declaration", definition,
                )
                return current
            params = list(concrete.params)
            params[receiver_index] = _utils._declared_nominal(
                owner if expose_member_receiver else variant.name, variant.generics,
            )
            exposed = replace(concrete, params=tuple(params), is_multi=True)
            self.env.overloads[definition.name][-1] = exposed
            self.env.context.define_structural_overload(definition.name, exposed)
            return current

        self_type = _utils._declared_nominal(owner, variant.generics)
        params = (FunctionParam(Symbol("self"), self_type), *contextual_params)
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            FunctionNode(
                params=params,
                body=definition.function.body,
                returns=requirement.overload.returns,
                where_clause=definition.function.where_clause,
                element_tags=definition.function.element_tags,
                element_tags_explicit=definition.function.element_tags_explicit,
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
        actual_tag_sets = {
            typing.overload.element_tags
            for typing in function.overloads
            if isinstance(typing.overload, T.Overload)
        }
        if actual_tag_sets != {requirement.overload.element_tags}:
            self._diagnose(
                f"variant member element '{definition.name}' element tags must "
                "exactly match the variant extend declaration",
                definition,
            )
            return typed_branch
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
        concrete = replace(concrete, implementation_id=declaration_id(
            self.source_file, definition.location, concrete,
        ))
        variant_type = _utils._declared_nominal(variant.name, variant.generics)
        exposed = replace(
            concrete,
            params=(self_type if expose_member_receiver else variant_type,
                    *requirement.overload.params),
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
        runtime_overload = replace(
            concrete,
            param_names=(Symbol("self"), *concrete.param_names[1:]),
        )
        return self._attach_method_bodies(typed_branch, ((
            concrete.implementation_id,
            TypedFunctionNode(function_node, typing.typ, (
                replace(typing, overload=runtime_overload),
            )),
        ),))

