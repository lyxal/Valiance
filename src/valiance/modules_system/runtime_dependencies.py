"""Private runtime declaration dependencies of module interfaces.

Runtime linkage is independent of the library consuming type metadata: a
constructor, reflection operation, serializer or a future runtime feature
must all see the same provider-qualified declaration graph.  The closure is
not an import surface and never grants source-level visibility.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Iterable

import valiance.vtypes as T
from valiance.asts import ASTNode, ObjectNode, Symbol
from valiance.asts.nodes import TypedElementNode, TypedNode
from valiance.modules_system.modules import (
    ModuleDefinition, ModuleExports, ModuleObject, ModuleTraitImplementation,
)
from valiance.vtypes.structural import nested_types


@dataclass(frozen=True)
class RuntimeDependencyClosure:
    """Required declarations and their canonical identities for one import."""

    declarations: tuple[ModuleObject, ...]
    executable_objects: tuple[ModuleObject, ...]
    origins: tuple[tuple[Symbol, Symbol], ...]

    def origins_for(self, declaration: ModuleObject) -> tuple[tuple[Symbol, Symbol], ...]:
        """Use the declaration's provider namespace, never a caller's alias."""
        if declaration.runtime_nominal_origins:
            return declaration.runtime_nominal_origins
        origin = declaration.origin
        if origin is None:
            return self.origins
        return tuple(
            (spelling, canonical) for spelling, canonical in self.origins
            if canonical.namespace == origin.namespace
        )



def provider_runtime_object(declaration: ModuleObject) -> TypedNode:
    """Include owned implementation methods in the private executable surface.

    A module's object declaration and its ``object X as Trait`` blocks are
    analysed separately. The public type surface stays unchanged, but a
    provider's imported callable still needs the checked method bodies when
    it dispatches on a private receiver. The module interface already carries
    their checked identities in ``declaration.typed``; preserve the matching
    source definitions for code generation without exposing an import name.
    """
    typed = declaration.typed
    node = typed.node
    if (not isinstance(node, ObjectNode)
            or node.kind != Symbol("object")
            or node.target is not None
            or not declaration.friendly_definitions
            or node.definitions == declaration.friendly_definitions):
        return typed
    return replace(typed, node=replace(
        node, definitions=declaration.friendly_definitions,
    ))


def _identity(declaration: ModuleObject, module_name: str) -> Symbol:
    return declaration.origin or Symbol(
        declaration.name.text, tuple(module_name.split("."))
    )


def _types_of_object(declaration: ModuleObject) -> Iterable[T.Type]:
    """Visit declared type contracts, not arbitrary executable AST bodies."""
    node = declaration.typed.node
    if not isinstance(node, ObjectNode):
        return
    if isinstance(declaration.typed.typ, T.Type):
        yield declaration.typed.typ
    for constraint in node.generic_constraints:
        if isinstance(constraint, T.Type):
            yield constraint
    for field in node.fields:
        if isinstance(field.typ, T.Type):
            yield field.typ
    for variant in node.variants:
        for field in variant.fields:
            if isinstance(field.typ, T.Type):
                yield field.typ
    for requirement in node.requirements:
        for param in requirement.params or ():
            if isinstance(param.typ, T.Type):
                yield param.typ
        for result in requirement.returns or ():
            if isinstance(result, T.Type):
                yield result
    for definition in (*node.definitions, *declaration.friendly_definitions):
        function = definition.function
        for param in function.params or ():
            if isinstance(param.typ, T.Type):
                yield param.typ
        for result in function.returns or ():
            if isinstance(result, T.Type):
                yield result


def _types_of_definition(declaration: ModuleDefinition) -> Iterable[T.Type]:
    if isinstance(declaration.typed.typ, T.Type):
        yield declaration.typed.typ
    for typing in declaration.typed.overloads:
        overload = typing.overload
        if overload is None:
            continue
        for attr in ("params", "returns", "generic_constraints"):
            for value in getattr(overload, attr, ()) or ():
                if isinstance(value, T.Type):
                    yield value


def _references(types: Iterable[T.Type]) -> Iterable[Symbol]:
    for typ in types:
        for part in nested_types(typ):
            if isinstance(part, T.NominalType):
                yield part.name



def _checked_calls(value: object) -> Iterable[Symbol]:
    """Find direct lexical calls in checked executable bodies, not source text.

    Imported calls that already carry a different runtime binding are not
    provider-local references. Only their original provider may link them.
    """
    visited: set[int] = set()

    def visit(item: object) -> Iterable[Symbol]:
        if isinstance(item, TypedElementNode):
            name = item.node.name
            if item.runtime_name in (None, name):
                yield name
        if isinstance(item, (ASTNode, T.Type, str, bytes)):
            return
        if isinstance(item, (tuple, list, frozenset)):
            for child in item:
                yield from visit(child)
        elif isinstance(item, dict):
            for child in item.values():
                yield from visit(child)
        elif is_dataclass(item):
            identity = id(item)
            if identity in visited:
                return
            visited.add(identity)
            for field in fields(item):
                if field.name in ("node", "typ", "overload", "dispatch_plan"):
                    continue
                yield from visit(getattr(item, field.name))

    yield from visit(value)


def _executable_object_dependencies(
    exports: ModuleExports,
) -> tuple[ModuleObject, ...]:
    """Find provider-owned constructors referenced by checked executable bodies.

    The provider prelude is cached once per module, even when its declarations
    are selected in separate import clauses. Compute its lexical dependencies
    from the *whole provider interface*, not the currently selected exports,
    so import order cannot change which hidden bindings get linked.
    """
    provider = tuple(exports.module_name.split("."))
    local_objects = {
        obj.name: obj for obj in exports.objects
        if isinstance(obj.typed.node, ObjectNode)
        and obj.typed.node.kind == Symbol("object")
        and obj.typed.node.target is None
        and _identity(obj, exports.module_name).namespace == provider
    }
    pending: list[object] = [
        *(definition.typed for definition in exports.definitions),
        *(obj.typed for obj in exports.objects),
        *(implementation.method_bodies for implementation in exports.trait_implementations),
    ]
    required: dict[Symbol, ModuleObject] = {}
    while pending:
        for name in _checked_calls(pending.pop()):
            obj = local_objects.get(name)
            if obj is None:
                continue
            origin = _identity(obj, exports.module_name)
            if origin not in required:
                required[origin] = obj
                pending.append(obj.typed)
    return tuple(required.values())


def resolve_runtime_dependencies(
    exports: ModuleExports,
    selected_objects: tuple[ModuleObject, ...],
    selected_definitions: tuple[ModuleDefinition, ...] = (),
    selected_implementations: tuple[ModuleTraitImplementation, ...] = (),
) -> RuntimeDependencyClosure:
    """Resolve the transitive, provider-scoped declaration graph of an import.

    The graph covers object/enum/variant/trait declarations and signature
    references through generics, unions, collections and structural types.
    It is computed from an analysed interface, so the same result is available
    whether that interface came from source or a serialized .vbcm artifact.
    """
    available: dict[Symbol, ModuleObject] = {}
    for declaration in (*exports.objects, *exports.runtime_dependencies):
        identity = _identity(declaration, exports.module_name)
        # A public alias and a private requirement may refer to one provider.
        # The original private declaration keeps its provider-local field names.
        available.setdefault(identity, declaration)

    def target(name: Symbol, owner: Symbol) -> Symbol | None:
        # The checked declaration is the authority for any local spelling of
        # an imported type. Looking up by short name alone would conflate
        # same-named declarations from distinct providers.
        owner_declaration = available.get(owner)
        if owner_declaration is not None:
            origins = dict(owner_declaration.runtime_nominal_origins)
            canonical = origins.get(name)
            if canonical is not None:
                # Missing recorded providers must not silently resolve to a
                # same-spelled declaration in the importing module.
                return canonical if canonical in available else None
        if name.namespace:
            if name in available:
                return name
            # A namespaced alias is valid only if it is recorded explicitly
            # by a declaration in this interface. Never search by short name.
            aliases = [
                identity for identity, item in available.items()
                if item.name == name
            ]
            return aliases[0] if len(aliases) == 1 else None
        matching = (
            identity for identity, item in available.items()
            if identity.namespace == owner.namespace
            and (identity.text == name.text or item.name == name)
        )
        return next(matching, None)

    root_ids = {
        _identity(obj, exports.module_name) for obj in selected_objects
    }
    # Preserve source order for reproducible runtime initialization.
    queue = list(dict.fromkeys(
        _identity(obj, exports.module_name) for obj in selected_objects
    ))
    owner_for_callable = Symbol("__module__", tuple(exports.module_name.split(".")))
    for definition in selected_definitions:
        declared_origins = dict(definition.private_nominals)
        for ref in _references(_types_of_definition(definition)):
            # Re-exported functions may have been checked against the original
            # provider's private nominal rather than the facade's namespace.
            canonical = declared_origins.get(ref, ref)
            resolved = (
                canonical if canonical in available else target(ref, owner_for_callable)
            )
            if resolved is not None:
                queue.append(resolved)
    for implementation in selected_implementations:
        patterns = (
            implementation.object_pattern, implementation.trait_pattern,
            *implementation.generic_constraints,
        )
        owner = Symbol("__implementation__", (
            implementation.provider.text if implementation.provider else
            exports.module_name.rsplit(".", 1)[-1],
        ))
        for ref in _references(typ for typ in patterns if isinstance(typ, T.Type)):
            resolved = target(ref, owner)
            if resolved is not None:
                queue.append(resolved)

    traversed: set[Symbol] = set()
    required: list[ModuleObject] = []
    while queue:
        identity = queue.pop(0)
        if identity in traversed:
            continue
        traversed.add(identity)
        declaration = available.get(identity)
        if declaration is None:
            continue
        if identity not in root_ids:
            required.append(declaration)
        for ref in _references(_types_of_object(declaration)):
            child = target(ref, identity)
            if child is not None and child not in traversed:
                queue.append(child)

    # The mapping is a runtime type fact, never a source-level import alias.
    # Keep provider namespaces separate even when declarations share a name.
    origins: list[tuple[Symbol, Symbol]] = []
    for identity, declaration in available.items():
        origin = declaration.runtime_nominal_origins
        if origin:
            for item in origin:
                if item not in origins:
                    origins.append(item)
        else:
            pair = (declaration.name, identity)
            if pair not in origins:
                origins.append(pair)
    executable = _executable_object_dependencies(exports)
    return RuntimeDependencyClosure(tuple(required), executable, tuple(origins))
