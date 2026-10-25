"""Compiler-side reflection metadata built from checked object declarations.

These records describe types and storage, without retaining object instances or
granting permission to access their values. They are not language runtime values.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    alternatives: tuple[FieldsDescriptor, ...] = ()

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
    if isinstance(typ, T.UnionType):
        evidence = tuple(field_type_evidence(item) for item in typ.items)
        if evidence and all(item is not None for item in evidence):
            return T.U(*(item[0] for item in evidence)), (
                T.U(*(item[1] for item in evidence)) if all(item[1] is not None for item in evidence) else None
            )
        return None
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


def fields_for_value(owner: T.Type, env: Environment) -> FieldsDescriptor:
    """Resolve storage evidence through nominal views, unions and variants."""
    owner = T.normalize(owner)
    if isinstance(owner, T.RowType) and not value_fields_need_specialization(owner, env):
        return fields_for_value(owner.base, env)
    if isinstance(owner, T.IntersectionType):
        alternatives = _intersection_layouts(owner, env)
        if alternatives:
            return FieldsDescriptor(TypeDescriptor(owner),
                                    tuple(field for layout in alternatives for field in layout.fields),
                                    alternatives)
    possibilities = owner.items if isinstance(owner, T.UnionType) else ()
    if isinstance(owner, T.NominalType) and (variant := env.lookup_variant(owner.name)) is not None:
        possibilities = tuple(T.N(member, *owner.args) for member in variant.members)
    if isinstance(owner, (T.TaggedType, T.NoVecType, T.ExactType)):
        inner = fields_for_value(owner.inner, env)
        if not inner.alternatives and inner.owner.typ != T.normalize(owner.inner):
            return fields_for_value(replace(owner, inner=inner.owner.typ), env)
        possibilities = tuple(replace(owner, inner=layout.owner.typ) for layout in inner.alternatives)
    if possibilities:
        layouts = tuple(fields_for_value(item, env) for item in possibilities)
        alternatives = tuple(dict.fromkeys(
            alternative for layout in layouts
            for alternative in (layout.alternatives or (layout,))
        ))
        return FieldsDescriptor(TypeDescriptor(owner),
                                tuple(field for layout in alternatives for field in layout.fields),
                                alternatives)
    return fields_for_type(owner, env)


def _storage_constraint(owner: T.Type) -> T.Type:
    """Project occurrence refinements to constraints on physical owner shapes."""
    owner = T.normalize(owner)
    if isinstance(owner, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _storage_constraint(owner.inner)
    if isinstance(owner, T.RowType):
        return _storage_constraint(owner.base)
    if isinstance(owner, T.UnionType):
        return T.U(*(_storage_constraint(item) for item in owner.items))
    if isinstance(owner, T.IntersectionType):
        return T.I(*(_storage_constraint(item) for item in owner.items))
    return owner


def _intersection_layouts(owner: T.IntersectionType, env: Environment) -> tuple[FieldsDescriptor, ...]:
    """Intersect finite owner evidence without inventing storage for open views."""
    candidates: list[FieldsDescriptor] = []
    for item in sorted(owner.items, key=T.show):
        if value_fields_need_specialization(item, env):
            continue
        metadata = fields_for_value(item, env)
        candidates.extend(metadata.alternatives or (metadata,))
    constraint = _storage_constraint(owner)
    return tuple(dict.fromkeys(layout for layout in candidates
                               if T.assignable(layout.owner.typ, constraint, env.context)))


def value_fields_need_specialization(owner: T.Type, env: Environment) -> bool:
    """Identify receiver views that do not determine a concrete storage layout."""
    owner = T.normalize(owner)
    if isinstance(owner, (T.TaggedType, T.NoVecType, T.ExactType)):
        return value_fields_need_specialization(owner.inner, env)
    if isinstance(owner, T.RowType):
        base = T.normalize(owner.base)
        while isinstance(base, (T.TaggedType, T.NoVecType, T.ExactType)):
            base = base.inner
        if isinstance(base, T.UnionType):
            return any(value_fields_need_specialization(T.Row(item, *owner.fields), env)
                       for item in base.items)
        if isinstance(base, T.IntersectionType):
            return value_fields_need_specialization(base, env)
        return not (isinstance(base, T.NominalType) and (
            env.lookup_object(base.name) is not None or env.lookup_variant(base.name) is not None
        ))
    if isinstance(owner, T.UnionType):
        return any(value_fields_need_specialization(item, env) for item in owner.items)
    if isinstance(owner, T.IntersectionType):
        return not _intersection_layouts(owner, env)
    if isinstance(owner, (T.VarType, T.AnonymousTraitType)):
        return True
    return isinstance(owner, T.NominalType) and env.lookup_trait(owner.name) is not None


def fields_type_owners(typ: T.Type) -> tuple[T.Type, ...] | None:
    """Recover the owners of a collection or a finite union of collections."""
    typ = T.normalize(typ)
    if isinstance(typ, T.UnionType):
        items = tuple(fields_type_owners(item) for item in typ.items)
        if items and all(item is not None for item in items):
            return tuple(owner for item in items for owner in item)
        return None
    if isinstance(typ, T.NominalType) and typ.name == Symbol("Fields") and len(typ.args) == 1:
        return (typ.args[0],)
    return None


def reflection_requires_unsafe(owner: T.Type, env: Environment) -> bool:
    """Conservatively detect possible transitive involvement of protected owners.

    This is an authorization question, not a duplication decision. Visiting a
    nominal declaration once along each path terminates recursive layouts, even
    when their recursive edges expand generic arguments. Arguments are checked
    before cutting a recursive edge so they cannot conceal protected storage.
    """
    def visit_view(typ: T.Type, active: frozenset[Symbol]) -> bool:
        """Check refinement evidence while selected layouts account for storage."""
        typ = T.normalize(typ)
        if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
            return visit_view(typ.inner, active)
        if isinstance(typ, T.RowType):
            return visit_view(typ.base, active) or any(visit(field.typ, active) for field in typ.fields)
        if isinstance(typ, (T.UnionType, T.IntersectionType)):
            return any(visit_view(item, active) for item in typ.items)
        if isinstance(typ, T.NominalType):
            return isinstance(typ, T.FFINamedType) or any(visit(argument, active) for argument in typ.args)
        if isinstance(typ, T.AnonymousTraitType):
            return any(isinstance(item, T.VarType) or (
                isinstance(item, T.NominalType) and any(visit(arg, active) for arg in item.args)
            ) for item in nested_types(typ))
        return visit(typ, active)

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
            variant = env.lookup_variant(typ.name)
            if variant is not None:
                if typ.name in active:
                    return False
                return any(visit(T.N(member, *typ.args), active | {typ.name})
                           for member in variant.members)
            # Built-in scalar representations do not contain managed owners.
            # Unresolved nominal categories (including traits) lack that proof.
            return typ not in (T.Int, T.String)
        if isinstance(typ, (T.FunctionType, T.OverloadSetType, T.TaskType,
                            T.AnonymousTraitType)):
            # A signature does not describe captures or a task's live state.
            return True
        if isinstance(typ, (T.NeverType, T.NoneTypeNode)):
            return False
        if isinstance(typ, T.IntersectionType):
            layouts = _intersection_layouts(typ, env)
            return (not layouts or any(visit(layout.owner.typ, active) for layout in layouts)
                    or visit_view(typ, active))
        if isinstance(typ, T.CollectionType):
            children = (typ.base,)
        elif isinstance(typ, T.TupleType):
            children = typ.params
        elif isinstance(typ, T.VariadicTupleType):
            children = tuple(item.typ for item in typ.items)
        elif isinstance(typ, T.RowType):
            children = (typ.base, *(field.typ for field in typ.fields))
        elif isinstance(typ, T.UnionType):
            children = typ.items
        else:
            return True
        return any(visit(child, active) for child in children)

    return visit(owner, frozenset())
