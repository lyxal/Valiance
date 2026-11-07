"""Focused imports declaration analysis."""

from __future__ import annotations

from dataclasses import field, replace

import valiance.vtypes as T
from valiance.asts import (
    DefineNode,
    ElementNode,
    ImportComponent,
    ImportPath,
    ImportSpec,
    ObjectNode,
    TypedFunctionNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedObjectNode,
    TypedNode,
)
from valiance.modules_system.modules import ModuleLoadError, ModuleObject, import_definitions
from valiance.asts.object_constructors import constructor_definitions
from valiance.vtypes.symbols import Symbol

from ..calls import callable_values as _functions
from ..support import analysis_utils as _utils
from ..state import AnalysisBranch
class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""


class _ImportDeclarations:
    """Own declaration operations for this domain."""

    def _load_import_definitions(
        self,
        spec: ImportSpec,
    ):
        """Load import definitions during static analysis."""
        if not spec.components and len(spec.path.parts) >= 2:
            module_path = ImportPath(spec.path.parts[:-1], spec.path.root)
            component = ImportComponent(Symbol(spec.path.parts[-1]))
            split_spec = ImportSpec(module_path, spec.alias, (component,))
            try:
                exports = self.module_loader.load(
                    split_spec.path,
                    current_file=self.source_file,
                )
                definitions = import_definitions(exports, split_spec)
            except ModuleLoadError:
                pass
            else:
                return exports, split_spec, definitions

        exports = self.module_loader.load(
            spec.path,
            current_file=self.source_file,
        )
        return exports, spec, import_definitions(exports, spec)

    def _import_source_text(
        self,
        spec: ImportSpec,
        visible_name: Symbol,
    ) -> str:
        """Render the source component that introduced an imported name."""
        path = spec.path.show()
        for component in spec.components:
            if (component.alias or component.name) == visible_name:
                return f"{path}.{component.name}"
        return f"{path}.{visible_name.text}"

    def _import_arity_diagnostic(
        self,
        message: str,
        spec: ImportSpec,
        name: Symbol,
    ) -> str:
        """Add concrete removal and namespacing fixes to an import conflict."""
        current = self._import_source_text(spec, name)
        previous = self._imported_definition_sources.get(name)
        current_module = spec.path.show()
        current_namespace = spec.alias or Symbol(spec.path.parts[-1])
        if previous is None:
            return (
                f"{message}\n"
                f"help: rename the imported element with "
                f"`import {{ {current} as {name}Imported }}`\n"
                f"help: or keep it namespaced with "
                f"`import {{ {current_module} }}` and use "
                f"`{current_namespace}.{name.text}`"
            )
        previous_module = previous.rsplit(".", 1)[0]
        previous_namespace = previous_module.rsplit(".", 1)[-1]
        return (
            f"{message}\n"
            f"help: either remove one of these imports: "
            f"`{previous}` or `{current}`\n"
            f"help: or keep both namespaced with "
            f"`import {{ {previous_module}, {current_module} }}` and use "
            f"`{previous_namespace}.{name.text}` or "
            f"`{current_namespace}.{name.text}`"
        )

    def _selected_import_overloads(self, typed_node: TypedFunctionNode) -> tuple[T.Overload, ...]:
        """Return the concrete overloads selected by one imported definition."""
        declared = tuple(
            typing.overload
            for typing in typed_node.overloads
            if isinstance(typing.overload, T.Overload)
        )
        if len(declared) == 1:
            return declared
        result = []
        for selected in _functions._callable_overloads(typed_node.typ):
            result.append(next(
                (
                    candidate
                    for candidate in declared
                    if candidate.params == selected.params
                    and candidate.returns == selected.returns
                ),
                selected,
            ))
        return tuple(result)

    def _is_redundant_imported_object(self, obj) -> bool:
        """Identify two paths to the *same* declaration, not same-named types.

        A diamond may re-export `base.Pair` through both `left.Pair` and
        `right.Pair`. The public spelling is identical, and the recorded
        provider origin proves they are one nominal declaration. Do not
        register its constructors or methods twice. Two different providers
        declaring `Pair` must still trigger the ordinary conflict diagnostic.
        """
        return (
            obj.origin is not None
            and obj.name in self._imported_object_sources
            and self.env.context.nominal_origins.get(obj.name) == obj.origin
        )

    def _validate_import_conflicts(
        self,
        resolved_spec: ImportSpec,
        definitions,
        objects,
    ) -> str | None:
        """Validate imported symbol and overload conflicts before mutation."""
        pending_overloads: dict[tuple[Symbol, tuple[T.Type, ...]], str] = {}
        for definition in definitions:
            source = self._import_source_text(resolved_spec, definition.name)
            for overload in self._selected_import_overloads(definition.typed):
                key = (definition.name, T.overload_parameter_shape(overload))
                previous = self._imported_overload_sources.get(key)
                current_pending = pending_overloads.get(key)
                conflict = previous or current_pending
                if conflict is not None and conflict != source:
                    return (
                        f"conflicting imported overload '{definition.name}' for parameter "
                        f"types {overload.params}: provided by `{conflict}` and `{source}`\n"
                        "help: select distinct overloads, exclude one with `except`, or alias one import"
                    )
                pending_overloads[key] = source

        pending_objects: dict[Symbol, str] = {}
        for obj in objects:
            source = self._import_source_text(resolved_spec, obj.name)
            previous = self._imported_object_sources.get(obj.name)
            current_pending = pending_objects.get(obj.name)
            conflict = previous or current_pending
            if (conflict is not None and conflict != source
                    and not self._is_redundant_imported_object(obj)):
                return (
                    f"conflicting imported symbol '{obj.name}': provided by "
                    f"`{conflict}` and `{source}`\n"
                    "help: alias one imported component or keep both modules namespaced"
                )
            if self._imported_definition_sources.get(obj.name) not in {None, source}:
                return f"conflicting imported symbol '{obj.name}'"
            pending_objects[obj.name] = source
        for definition in definitions:
            source = self._import_source_text(resolved_spec, definition.name)
            if self._imported_object_sources.get(definition.name) not in {None, source}:
                return f"conflicting imported symbol '{definition.name}'"
        return None

    def _commit_import_sources(self, resolved_spec: ImportSpec, definitions, objects) -> None:
        """Commit validated imported identities for later conflict checks."""
        for definition in definitions:
            source = self._import_source_text(resolved_spec, definition.name)
            self._imported_definition_sources[definition.name] = source
            for overload in self._selected_import_overloads(definition.typed):
                self._imported_overload_sources[(definition.name, T.overload_parameter_shape(overload))] = source
        for obj in objects:
            self._imported_object_sources[obj.name] = self._import_source_text(
                resolved_spec, obj.name
            )

    def _register_imported_definition(
        self,
        name: Symbol,
        typed_node: TypedFunctionNode,
        runtime_name: Symbol,
        dependencies: tuple[TypedNode, ...] = (),
        private_nominals: dict[Symbol, Symbol] | None = None,
    ) -> None:
        """Register imported definition with provider-owned private type identities."""
        from valiance.modules_system.modules import _rename_imported_types
        for overload in self._selected_import_overloads(typed_node):
            if private_nominals:
                # Imported signatures must not confuse private provider types
                # with caller-local names. Leave call-site-checked bodies in
                # their provider lexical scope; they carry private helpers.
                overload = replace(
                    overload,
                    params=_rename_imported_types(overload.params, private_nominals),
                    returns=_rename_imported_types(overload.returns, private_nominals),
                    generic_constraints=_rename_imported_types(
                        overload.generic_constraints, private_nominals
                    ),
                    # A reflection-specialized generic body is rechecked at
                    # the call site. Adapt its *typed nominal references* to
                    # the same selected aliases as its public signature;
                    # otherwise `fields[Box[T]]` remains inaccessible when
                    # the only visible spelling is `Renamed[T]`.
                    call_site_body=(
                        (overload.call_site_body[0], _rename_imported_types(
                            overload.call_site_body[1], private_nominals,
                        ))
                        if isinstance(overload.call_site_body, tuple)
                        and len(overload.call_site_body) == 2
                        else overload.call_site_body
                    ),
                )
            self.env.define_overload(name, overload)
            if dependencies and overload.call_site_body is not None and overload.implementation_id is not None:
                self.env.call_site_dependencies[overload.implementation_id] = dependencies
            self.env.bind_runtime_name(
                name,
                runtime_name,
                overload,
            )

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
            # Imported stdlib object methods may call a native element in their
            # body. Restore its *actual* signature, rather than synthesizing a
            # receiver-shaped placeholder from the friendly method's signature.
            if len(runtime_name.namespace) == 2 and runtime_name.namespace[0] == 'std':
                from valiance.elements.stdlib_native import _native_functions, _runtime_element
                native = next((
                    fn for fn in (_native_functions(runtime_name.namespace[1]) or ())
                    if fn.name.text == runtime_name.text
                ), None)
                if native is not None:
                    element = _runtime_element(runtime_name.namespace[1], native)
                    for overload in element.overloads:
                        if native.owner is not None and native.owner.text == owner.text:
                            overload = replace(
                                overload,
                                params=(T.N(owner), *overload.params[1:]),
                            )
                        self.env.define_overload(element.name, overload)
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
        dependencies: tuple[TypedNode, ...] = (),
    ) -> None:
        """Rebuild an imported surface without replaying its checked-body warnings."""
        warning_count = len(self.warnings)
        try:
            self._register_imported_object_surface(obj, runtime_name, dependencies)
        finally:
            del self.warnings[warning_count:]

    def _register_imported_object_surface(
        self,
        obj,
        runtime_name: Symbol,
        dependencies: tuple[TypedNode, ...] = (),
    ) -> None:
        """Restore imported declarations and retain their private default-argument scope."""
        if dependencies:
            # Imported bodies were written in the provider's lexical scope.
            # Its private dependencies must be available while rebuilding the
            # callable surface, without becoming imports in the caller.
            caller = self.env
            dependency_env = T.Environment(
                context=caller.context.copy(), parent=caller.parent,
            )
            self.env = dependency_env
            try:
                for dependency in dependencies:
                    if isinstance(dependency, TypedImportedObjectNode):
                        dependency_object = ModuleObject(
                            dependency.node.name, dependency,
                        )
                        self._register_imported_object(
                            dependency_object, dependency.runtime_name,
                        )
                        # Compiled provider metadata records canonical qualified
                        # declarations (e.g. std.file.Encoding), while an
                        # imported constructor's checked defaults can use the
                        # provider-local spelling (file.Encoding). Both names
                        # must resolve to the *same* private declaration when
                        # checking that constructor. Never install the alias
                        # in the consumer's public environment.
                        canonical = dependency.node.name
                        if len(canonical.namespace) > 1:
                            local_name = Symbol(
                                canonical.text, (canonical.namespace[-1],),
                            )
                            self._register_imported_object(
                                replace(dependency_object, name=local_name),
                                dependency.runtime_name,
                            )
                    elif isinstance(dependency, TypedImportedFunctionNode):
                        self._register_imported_definition(
                            dependency.node.name, dependency,
                            dependency.runtime_name,
                        )
            finally:
                self.env = caller
            # Defaults can produce private case types, including under a
            # provider-local alias. Share relation facts without exposing names.
            for member, parent in dependency_env.context.variant_members.items():
                if parent.namespace:
                    caller.context.variant_members.setdefault(member, parent)
                    for name in (member, parent):
                        if name in dependency_env.context.generic_variance:
                            caller.context.generic_variance.setdefault(
                                name, dependency_env.context.generic_variance[name],
                            )
            previous_parent = caller.parent
            caller.parent = dependency_env
            try:
                self._register_imported_object(obj, runtime_name)
                for overload in caller.overloads_for(obj.name):
                    if any(overload.param_defaults):
                        caller.default_argument_scopes[
                            (overload.implementation_id, overload)
                        ] = dependency_env
            finally:
                caller.parent = previous_parent
            return

        node = obj.typed.node
        if not isinstance(node, ObjectNode):
            return
        if node.target is not None and obj.name in self.env.context.variant_members:
            # A case implementation supplies methods, not a constructor binding.
            return
        self.env.bind_runtime_name(obj.name, runtime_name)
        if obj.name != node.name:
            self.env.runtime_nominal_names[obj.name] = node.name
            # The provider's qualified declaration spelling can still appear
            # in another imported callable's checked signature. Record its
            # nominal origin separately from the caller-visible alias so both
            # names denote the same declaration during overload selection.
            # Do not install a source-visible import for the provider spelling.
            if node.name.namespace:
                origin = getattr(obj, "origin", None)
                if origin is not None:
                    existing = self.env.context.nominal_origins.get(node.name)
                    if existing is None or existing == origin:
                        self.env.context.nominal_origins[node.name] = origin
            self._register_imported_object(replace(obj, name=node.name), runtime_name)
            # A selected native object has a source alias, but its values must
            # keep the nominal identity used by native module signatures.
            for registry in ("objects", "traits", "variants", "constructors"):
                declarations = getattr(self.env, registry)
                if node.name in declarations:
                    # A scoped alias is a separate lookup spelling for the
                    # same declaration, but its checked descriptor must keep
                    # the spelling being resolved. Otherwise nominal-type
                    # resolution rewrites `alpha.Box` back to `Box`, making
                    # unrelated providers collide during generic reflection.
                    declarations[obj.name] = replace(
                        declarations[node.name], name=obj.name,
                    )
            for overload in self.env.overloads_for(node.name):
                # Constructor aliases are lexical names for their provider's
                # nominal type. Preserve the caller-visible spelling in their
                # checked result, while keeping the original runtime binding.
                from valiance.modules_system.modules import _rename_imported_types
                self.env.define_overload(obj.name, replace(
                    overload,
                    returns=_rename_imported_types(
                        overload.returns, {node.name: obj.name},
                    ),
                ))
            if node.kind == Symbol("variant"):
                self._define_variant_shape(replace(node, name=obj.name))
                for member in node.variants:
                    names = [member.name]
                    if (
                        all(member.fixed_value and field.name == Symbol("value")
                            for field in member.fields)
                        and not member.name.text.startswith("\\")
                    ):
                        names.append(Symbol(f"\\{member.name.text}"))
                    for name in names:
                        canonical = _utils._child_symbol(node.name, name)
                        visible = _utils._child_symbol(obj.name, name)
                        self.env.bind_runtime_name(visible, canonical)
                        self.env.runtime_nominal_names[visible] = canonical
            return
        kind = node.kind.text
        if kind == "trait":
            if obj.name not in self.env.traits:
                self._define_trait_shape(obj.name, node)
            if obj.import_friendly:
                self._register_imported_friendly_runtime_elements(obj.name, node.definitions)
                bodies = dict(obj.typed.method_bodies) if isinstance(obj.typed, TypedObjectNode) else {}
                identities = dict(obj.typed.method_ids) if isinstance(obj.typed, TypedObjectNode) else {}
                for definition in node.definitions:
                    records = dict(identities.get(definition.name.text, ()))
                    location = definition.location.offset if definition.location is not None else None
                    for _shape, identity in records.get(location, ()):
                        checked = bodies.get(identity)
                        if checked is None:
                            continue
                        for overload in self._selected_import_overloads(checked):
                            overload = replace(overload, implementation_id=identity, is_multi=definition.is_multi)
                            qualified = Symbol(f"{obj.name}::{definition.name.text}")
                            runtime_method = Symbol(f"{obj.name}::{definition.name}")
                            self.env.define_overload(definition.name, overload, object_friendly=True)
                            self.env.define_overload(qualified, overload)
                            self.env.bind_runtime_name(qualified, runtime_method, overload)
                            self.env.bind_runtime_name(definition.name, runtime_method, overload)
            return
        if kind == "variant":
            self._define_variant_shape(node)
            if obj.import_friendly:
                self._register_imported_friendly_runtime_elements(
                    obj.name, node.definitions,
                )
                bodies = (dict(obj.typed.method_bodies)
                          if isinstance(obj.typed, TypedObjectNode) else {})
                identities = (dict(obj.typed.method_ids)
                              if isinstance(obj.typed, TypedObjectNode) else {})
                for definition in node.definitions:
                    if definition.visibility != Symbol("public"):
                        continue
                    records = dict(identities.get(definition.name.text, ()))
                    location = (definition.location.offset
                                if definition.location is not None else None)
                    for _shape, identity in records.get(location, ()):
                        checked = bodies.get(identity)
                        if checked is None:
                            continue
                        for overload in self._selected_import_overloads(checked):
                            from valiance.modules_system.modules import _rename_imported_types
                            original_owner = Symbol(node.name.text)
                            renamed_types = {original_owner: obj.name}
                            overload = replace(
                                overload,
                                params=_rename_imported_types(overload.params, renamed_types),
                                returns=_rename_imported_types(overload.returns, renamed_types),
                                implementation_id=identity, is_multi=True,
                            )
                            qualified = Symbol(f"{obj.name}::{definition.name}")
                            runtime_method = definition.name
                            self.env.define_overload(
                                definition.name, overload, object_friendly=True,
                            )
                            self.env.define_overload(qualified, overload)
                            self.env.bind_runtime_name(
                                definition.name, runtime_method, overload,
                            )
                            self.env.bind_runtime_name(
                                qualified, runtime_method, overload,
                            )
            return
        if kind != "object" or node.target is not None:
            return

        object_attributes = self._object_attributes(
            node.fields, node.generics, node.generic_scope_id,
        )
        if object_attributes is None:
            return
        defaults = frozenset(field.name for field in node.fields if field.default)
        constructors = constructor_definitions(obj.name, node.definitions)
        friendly_definitions = tuple(
            definition
            for definition in node.definitions
            if definition not in constructors
            # Destructor bytecode is linked with an imported constructor, but
            # the lifecycle hook is never exposed as a callable import.
            and not definition.name.text.startswith("~")
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
                implementation_ids=(
                    dict(obj.typed.method_ids)
                    if isinstance(obj.typed, TypedObjectNode) else None
                ),
            )
