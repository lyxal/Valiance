"""Compiler-side reflection metadata built from checked object declarations.

These records describe types and storage, without retaining object instances or
granting permission to access their values. They are not language runtime values.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import valiance.vtypes as T
from valiance.vtypes.structural import nested_types
from valiance.vtypes.symbols import Symbol

if TYPE_CHECKING:
    from valiance.vtypes.environment import Environment


class Visibility(Enum):
    """The existing object field access categories."""

    PUBLIC = "public"
    READABLE = "readable"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class TypeDescriptor:
    """Exact normalized type evidence, including arguments and data tags."""

    typ: T.Type

    def __post_init__(self) -> None:
        """Canonicalize equivalent type expressions for descriptor identity."""
        object.__setattr__(self, "typ", T.normalize(self.typ))


@dataclass(frozen=True, slots=True)
class FieldDescriptor:
    """One declared storage slot identified by its owner and declaration index."""

    owner: TypeDescriptor
    name: str
    index: int
    type: TypeDescriptor
    visibility: Visibility


@dataclass(frozen=True, slots=True)
class FieldsDescriptor:
    """An owner's heterogeneous storage metadata in declaration order."""

    owner: TypeDescriptor
    fields: tuple[FieldDescriptor, ...] = ()

    @property
    def value_type(self) -> T.Type:
        """Return all possible stored value types, or bottom for empty storage."""
        return T.U(*(field.type.typ for field in self.fields))

    def find(self, name: str) -> FieldDescriptor | None:
        """Look up metadata by name without imposing language panic semantics."""
        return next((field for field in self.fields if field.name == name), None)


def field_type_evidence(typ: T.Type) -> tuple[T.Type, T.Type | None] | None:
    """Recover an owner and an optional opened field witness from a descriptor view."""
    typ = T.normalize(typ)
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return field_type_evidence(typ.inner)
    if isinstance(typ, T.RowType):
        evidence = field_type_evidence(typ.base)
        if evidence is None:
            return None
        for field in typ.fields:
            field_type = T.normalize(field.typ)
            if (field.name == Symbol("type") and isinstance(field_type, T.NominalType)
                    and field_type.name == Symbol("Type") and len(field_type.args) == 1):
                return evidence[0], field_type.args[0]
        return evidence
    if (isinstance(typ, T.NominalType) and typ.name == Symbol("Field")
            and len(typ.args) == 1):
        return typ.args[0], None
    return None


def opened_field_type(field: FieldDescriptor) -> T.Type:
    """Refine a public Field[Owner] using its concrete, immutable type property."""
    return T.Row(T.N(Symbol("Field"), field.owner.typ),
                 T.Field(Symbol("type"), T.N(Symbol("Type"), field.type.typ)))


def fields_for_type(owner: T.Type, env: Environment) -> FieldsDescriptor:
    """Describe managed nominal storage, including private and generic fields.

    Other type categories have no declared object storage. Wrappers retain their
    exact owner identity while consulting the underlying nominal declaration.
    Callers must enforce reflection authorization before exposing this metadata
    to a source program.
    """
    descriptor = TypeDescriptor(owner)
    storage = descriptor.typ
    while isinstance(storage, (T.TaggedType, T.NoVecType, T.ExactType)):
        storage = storage.inner
    definition = (
        env.lookup_object(storage.name)
        if isinstance(storage, T.NominalType)
        and not isinstance(storage, T.FFINamedType)
        else None
    )
    if definition is None:
        return FieldsDescriptor(descriptor)

    arguments = dict(zip(definition.generics, storage.args, strict=False))
    fields: list[FieldDescriptor] = []
    for index, attribute in enumerate(definition.attributes):
        # Source generics carry binder identities; hand-built declarations may
        # still use unscoped variables. Substitute by the variable's actual key.
        substitution = {
            T.type_var_key(variable): arguments[Symbol(variable.name)]
            for variable in nested_types(attribute.typ)
            if isinstance(variable, T.VarType)
            and Symbol(variable.name) in arguments
        }
        fields.append(FieldDescriptor(
            owner=descriptor,
            name=attribute.name.text,
            index=index,
            type=TypeDescriptor(T._substitute(attribute.typ, substitution)),
            visibility=Visibility(attribute.access.text),
        ))
    return FieldsDescriptor(descriptor, tuple(fields))


def reflection_requires_unsafe(owner: T.Type, env: Environment) -> bool:
    """Conservatively detect possible transitive involvement of protected owners.

    This is an authorization question, not a duplication decision. Visiting a
    nominal declaration once along each path terminates recursive layouts, even
    when their recursive edges expand generic arguments. Arguments are checked
    before cutting a recursive edge so they cannot conceal protected storage.
    """
    def visit(typ: T.Type, active: frozenset[Symbol]) -> bool:
        """Follow stored representation and type evidence on one graph path."""
        typ = T.normalize(typ)
        if isinstance(typ, T.VarType):
            return True
        if isinstance(typ, (T.NoVecType, T.ExactType)):
            return visit(typ.inner, active)
        if isinstance(typ, T.TaggedType):
            return visit(typ.inner, active)
        if isinstance(typ, T.NominalType):
            if any(visit(argument, active) for argument in typ.args):
                return True
            if isinstance(typ, T.FFINamedType):
                return True
            definition = env.lookup_object(typ.name)
            if definition is not None:
                if definition.duplication_error is not None:
                    return True
                if typ.name in active:
                    return False
                return any(
                    visit(field.type.typ, active | {typ.name})
                    for field in fields_for_type(typ, env).fields
                )
            # Built-in scalar representations do not contain managed owners.
            # Unresolved nominal categories (including traits) lack that proof.
            return typ not in (T.Int, T.String)
        if isinstance(typ, (T.FunctionType, T.OverloadSetType, T.TaskType,
                            T.AnonymousTraitType)):
            # A signature does not describe captures or a task's live state.
            return True
        if isinstance(typ, (T.NeverType, T.NoneTypeNode)):
            return False
        if isinstance(typ, (T.CollectionType, T.TupleType, T.VariadicTupleType,
                            T.RowType, T.UnionType, T.IntersectionType)):
            return any(visit(child, active) for child in tuple(nested_types(typ))[1:])
        return True

    return visit(owner, frozenset())
