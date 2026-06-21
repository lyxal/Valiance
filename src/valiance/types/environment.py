from __future__ import annotations

"""Compiler-facing environment for symbols and relationship facts."""

from dataclasses import dataclass, field

from valiance.types.context import Context
from valiance.types.nodes import Overload, OverloadSetType, Type
from valiance.types.stack import StackApplication, TypeStack


@dataclass
class Environment:
    """Compiler-facing registry for symbols and type relationship facts."""

    context: Context = field(default_factory=Context)
    variables: dict[str, Type] = field(default_factory=dict[str, Type])
    overloads: dict[str, list[Overload]] = field(
        default_factory=dict[str, list[Overload]]
    )

    def define_variable(self, name: str, typ: Type) -> None:
        """Register or replace a named variable/value type."""
        self.variables[name] = typ

    def lookup_variable(self, name: str) -> Type | None:
        """Return a named variable/value type, if one exists."""
        return self.variables.get(name)

    def define_overload(self, name: str, overload: Overload) -> None:
        """Append one overload to a named overload set."""
        self.overloads.setdefault(name, []).append(overload)

    def overloads_for(self, name: str) -> tuple[Overload, ...]:
        """Return the overload candidates registered for ``name``."""
        return tuple(self.overloads.get(name, ()))

    def value_type(self, name: str) -> Type | None:
        """Return the type of a named value or overload set."""
        if name in self.variables:
            return self.variables[name]
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
    ) -> StackApplication | None:
        """Resolve and apply a named overload set to ``stack``."""
        overloads = self.overloads_for(name)
        if not overloads:
            return None
        return stack.apply(
            overloads,
            self.context,
            infer_missing=infer_missing,
        )
