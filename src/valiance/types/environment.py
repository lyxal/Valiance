"""Compiler-facing environment for symbols and relationship facts."""

from __future__ import annotations

from dataclasses import dataclass, field

from valiance.types.context import Context
from valiance.types.nodes import NeverType, Overload, OverloadSetType, Type
from valiance.types.stack import StackApplication, TypeStack


class EnvironmentApplyResult:
    """Base class for applying a named environment entry to a stack."""


@dataclass(frozen=True)
class AppliedElement(EnvironmentApplyResult):
    """A named overload set matched the stack and produced an application."""

    application: StackApplication


@dataclass(frozen=True)
class UnknownElement(EnvironmentApplyResult):
    """No overload set exists for the requested element name."""

    name: str


@dataclass(frozen=True)
class NoMatchingOverload(EnvironmentApplyResult):
    """A known overload set failed, but still has a fixed stack effect."""

    name: str
    overloads: tuple[Overload, ...]
    stack: TypeStack
    params: tuple[Type, ...]
    actual_returns: tuple[Type, ...]


@dataclass(frozen=True)
class ObjectAttribute:
    """One typed attribute declared on an object type."""

    name: str
    typ: Type


@dataclass(frozen=True)
class ObjectDefinition:
    """The structural facts known about one object type in scope."""

    name: str
    attributes: tuple[ObjectAttribute, ...] = ()

    def attribute_type(self, name: str) -> Type | None:
        """Return an attribute's type, if the object declares it."""
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute.typ
        return None


@dataclass
class Environment:
    """Compiler-facing registry for symbols and type relationship facts."""

    context: Context = field(default_factory=Context)
    parent: Environment | None = None
    variables: dict[str, Type] = field(default_factory=dict[str, Type])
    overloads: dict[str, list[Overload]] = field(
        default_factory=dict[str, list[Overload]]
    )
    objects: dict[str, ObjectDefinition] = field(
        default_factory=dict[str, ObjectDefinition]
    )

    def define_variable(self, name: str, typ: Type) -> None:
        """Register or replace a variable in this environment frame."""
        self.variables[name] = typ

    def child_scope(self) -> Environment:
        """Return a child frame that can read this environment."""
        return Environment(context=self.context, parent=self)

    def lookup_local_variable(self, name: str) -> Type | None:
        """Return a variable from this frame only."""
        return self.variables.get(name)

    def lookup_variable(self, name: str) -> Type | None:
        """Return a named variable/value type from this frame or an outer one."""
        if name in self.variables:
            return self.variables[name]
        if self.parent is not None:
            return self.parent.lookup_variable(name)
        return None

    def define_temporary_variable(self, name: str, typ: Type) -> None:
        """Bind a short-lived variable in this frame."""
        if name in self.variables:
            raise ValueError(f"temporary variable {name!r} already exists")
        self.variables[name] = typ

    def drop_local_variable(self, name: str) -> None:
        """Remove a variable from this frame, if present."""
        self.variables.pop(name, None)

    def define_object(
        self,
        name: str,
        attributes: tuple[ObjectAttribute, ...] = (),
    ) -> None:
        """Register or replace an object type visible in this environment."""
        seen: set[str] = set()
        for attribute in attributes:
            if attribute.name in seen:
                raise ValueError(
                    f"object {name!r} declares attribute {attribute.name!r} twice"
                )
            seen.add(attribute.name)
        self.objects[name] = ObjectDefinition(name, attributes)

    def lookup_object(self, name: str) -> ObjectDefinition | None:
        """Return an object definition, if one exists in scope."""
        if name in self.objects:
            return self.objects[name]
        if self.parent is not None:
            return self.parent.lookup_object(name)
        return None

    def object_exists(self, name: str) -> bool:
        """Return whether an object type exists in scope."""
        return self.lookup_object(name) is not None

    def lookup_attribute(self, object_name: str, attribute_name: str) -> Type | None:
        """Return the declared type of ``object_name.attribute_name``."""
        definition = self.lookup_object(object_name)
        if definition is None:
            return None
        return definition.attribute_type(attribute_name)

    def has_attribute(self, object_name: str, attribute_name: str) -> bool:
        """Return whether an object declares the requested attribute."""
        return self.lookup_attribute(object_name, attribute_name) is not None

    def define_overload(self, name: str, overload: Overload) -> None:
        """Append one overload to a named overload set."""
        candidates = self.overloads.setdefault(name, [])
        if candidates:
            expected_arity = len(candidates[0].params)
            expected_returns = len(candidates[0].returns)
            if len(overload.params) != expected_arity:
                raise ValueError(
                    f"overloads for {name!r} must all take {expected_arity} "
                    f"inputs, got {len(overload.params)}"
                )
            if len(overload.returns) != expected_returns:
                raise ValueError(
                    f"overloads for {name!r} must all return {expected_returns} "
                    f"values, got {len(overload.returns)}"
                )
        candidates.append(overload)

    def overloads_for(self, name: str) -> tuple[Overload, ...]:
        """Return the overload candidates registered for ``name``."""
        local = tuple(self.overloads.get(name, ()))
        if self.parent is None:
            return local
        return local + self.parent.overloads_for(name)

    def value_type(self, name: str) -> Type | None:
        """Return the type of a named value or overload set."""
        variable = self.lookup_variable(name)
        if variable is not None:
            return variable
        overloads = self.overloads_for(name)
        if overloads:
            return OverloadSetType(overloads)
        return None

    def add_trait_impl(self, type_name: str, trait_name: str) -> None:
        """Record that a concrete type implements a trait."""
        self.context.trait_impls.setdefault(type_name, set()).add(trait_name)

    def add_trait_parent(self, trait_name: str, parent_name: str) -> None:
        """Record that one trait implies another trait."""
        self.context.trait_parents.setdefault(trait_name, set()).add(parent_name)

    def add_variant_member(self, member_name: str, variant_name: str) -> None:
        """Record that a nominal type belongs to a variant."""
        self.context.variant_members[member_name] = variant_name

    def add_unit_tag(self, tag: str) -> None:
        """Record a tag that cannot be silently erased."""
        self.context.unit_tags.add(tag)

    def apply(
        self,
        name: str,
        stack: TypeStack,
        *,
        infer_missing: bool = False,
    ) -> EnvironmentApplyResult:
        """Resolve and apply a named overload set to ``stack``."""
        overloads = self.overloads_for(name)
        if not overloads:
            return UnknownElement(name)
        applied = stack.apply(
            overloads,
            self.context,
            infer_missing=infer_missing,
        )
        if applied is None:
            params, actual_returns = _failed_application_shape(overloads)
            remaining = stack.items if not params else stack.items[: -len(params)]
            return NoMatchingOverload(
                name,
                overloads,
                TypeStack(remaining + actual_returns),
                params,
                actual_returns,
            )
        return AppliedElement(applied)


def _failed_application_shape(
    overloads: tuple[Overload, ...],
) -> tuple[tuple[Type, ...], tuple[Type, ...]]:
    """Return the fixed stack shape for a failed known overload set."""
    params = overloads[0].params
    return_count = len(overloads[0].returns)
    return params, tuple(NeverType() for _ in range(return_count))
