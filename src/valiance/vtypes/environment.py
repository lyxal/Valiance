"""Compiler-facing environment for symbols and relationship facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from valiance.vtypes.symbols import Symbol
from valiance.vtypes.context import Context, TagKind, Variance
from valiance.vtypes.nodes import (
    FunctionType,
    GenericConstraint,
    NeverType,
    NominalType,
    Overload,
    OverloadSetType,
    Type,
    VariadicTupleType,
)
from valiance.vtypes.stack import StackApplication, TypeStack


class EnvironmentApplyResult:
    """Base class for applying a named environment entry to a stack."""


@dataclass(frozen=True)
class AppliedElement(EnvironmentApplyResult):
    """A named overload set matched the stack and produced an application."""

    application: StackApplication


@dataclass(frozen=True)
class UnknownElement(EnvironmentApplyResult):
    """No overload set exists for the requested element name."""

    name: Symbol


class ElementTagKind(Enum):
    """Static categories for element/function tags."""

    PROPERTY = auto()
    COMPANION = auto()


@dataclass(frozen=True)
class ElementTagDefinition:
    """One element-tag declaration visible in an environment scope."""

    name: Symbol
    kind: ElementTagKind


@dataclass(frozen=True)
class NoMatchingOverload(EnvironmentApplyResult):
    """A known overload set failed, but still has a fixed stack effect."""

    name: Symbol
    overloads: tuple[Overload, ...]
    stack: TypeStack
    params: tuple[Type, ...]
    actual_returns: tuple[Type, ...]


@dataclass(frozen=True)
class ObjectAttribute:
    """One typed attribute declared on an object type."""

    name: Symbol
    typ: Type
    access: Symbol = Symbol("readable")
    has_default: bool = False


@dataclass(frozen=True)
class ObjectDefinition:
    """The structural facts known about one object type in scope."""

    name: Symbol
    generics: tuple[Symbol, ...] = ()
    generic_variance: tuple[Variance, ...] = ()
    attributes: tuple[ObjectAttribute, ...] = ()

    def attribute_type(self, name: Symbol) -> Type | None:
        """Return an attribute's type, if the object declares it."""
        attribute = self.attribute(name)
        return None if attribute is None else attribute.typ

    def attribute(self, name: Symbol) -> ObjectAttribute | None:
        """Return an attribute, if the object declares it."""
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute
        return None


@dataclass(frozen=True)
class TraitRequirement:
    """One required element signature for a trait-like interface."""

    name: Symbol
    overload: Overload


@dataclass(frozen=True)
class TraitDefinition:
    """The static facts known about a trait."""

    name: Symbol
    generics: tuple[Symbol, ...] = ()
    generic_variance: tuple[Variance, ...] = ()
    requirements: tuple[TraitRequirement, ...] = ()


@dataclass(frozen=True)
class VariantDefinition:
    """The static facts known about a closed variant."""

    name: Symbol
    generics: tuple[Symbol, ...] = ()
    generic_variance: tuple[Variance, ...] = ()
    members: tuple[Symbol, ...] = ()
    requirements: tuple[TraitRequirement, ...] = ()


@dataclass(frozen=True)
class EnumMemberDefinition:
    """One statically declared enum member."""

    name: Symbol
    typ: Type | None = None
    has_value: bool = False


@dataclass(frozen=True)
class EnumDefinition:
    """The static facts known about an enum."""

    name: Symbol
    value_type: Type | None = None
    members: tuple[EnumMemberDefinition, ...] = ()


@dataclass(frozen=True)
class ConstructorDefinition:
    """Runtime constructor metadata for a nominal structured value."""

    name: Symbol
    fields: tuple[ObjectAttribute, ...]
    defaults: frozenset[Symbol] = frozenset()
    generic_constraints: tuple[GenericConstraint, ...] = ()

    @property
    def required_fields(self) -> tuple[ObjectAttribute, ...]:
        """Return fields that must be initialized before construction completes."""
        return tuple(field for field in self.fields if field.name not in self.defaults)


@dataclass(frozen=True)
class DataTagDefinition:
    """One data-tag declaration visible in an environment scope."""

    name: Symbol
    kind: TagKind


@dataclass(frozen=True)
class TagOverlayDefinition:
    """One tag-aware signature overlay for an existing element."""

    tag: Symbol
    element: Symbol
    overload: Overload
    public: bool = False


@dataclass
class Environment:
    """Compiler-facing registry for symbols and type relationship facts."""

    context: Context = field(default_factory=Context)
    parent: Environment | None = None
    overloads: dict[Symbol, list[Overload]] = field(
        default_factory=dict[Symbol, list[Overload]]
    )
    object_friendly_overloads: dict[Symbol, set[int]] = field(
        default_factory=dict[Symbol, set[int]]
    )
    objects: dict[Symbol, ObjectDefinition] = field(
        default_factory=dict[Symbol, ObjectDefinition]
    )
    traits: dict[Symbol, TraitDefinition] = field(
        default_factory=dict[Symbol, TraitDefinition]
    )
    variants: dict[Symbol, VariantDefinition] = field(
        default_factory=dict[Symbol, VariantDefinition]
    )
    enums: dict[Symbol, EnumDefinition] = field(
        default_factory=dict[Symbol, EnumDefinition]
    )
    constructors: dict[Symbol, ConstructorDefinition] = field(
        default_factory=dict[Symbol, ConstructorDefinition]
    )
    data_tags: dict[Symbol, DataTagDefinition] = field(
        default_factory=dict[Symbol, DataTagDefinition]
    )
    element_tags: dict[Symbol, ElementTagDefinition] = field(
        default_factory=dict[Symbol, ElementTagDefinition]
    )
    tag_parents: dict[Symbol, Symbol] = field(default_factory=dict[Symbol, Symbol])
    disjoint_tags: dict[Symbol, set[Symbol]] = field(
        default_factory=dict[Symbol, set[Symbol]]
    )
    disjoint_element_tags: dict[Symbol, set[Symbol]] = field(
        default_factory=dict[Symbol, set[Symbol]]
    )
    disjoint_data_element_tags: dict[Symbol, set[Symbol]] = field(
        default_factory=dict[Symbol, set[Symbol]]
    )
    tag_overlays: dict[Symbol, list[TagOverlayDefinition]] = field(
        default_factory=dict[Symbol, list[TagOverlayDefinition]]
    )
    tag_attached_elements: dict[Symbol, set[Symbol]] = field(
        default_factory=dict[Symbol, set[Symbol]]
    )
    tag_validator_static_results: dict[Symbol, dict[int, bool]] = field(
        default_factory=dict[Symbol, dict[int, bool]]
    )
    runtime_names: dict[Symbol, Symbol] = field(default_factory=dict[Symbol, Symbol])

    def child_scope(self) -> Environment:
        """Return a child frame that can read this environment."""
        return Environment(context=self.context, parent=self)

    def lexical_child_scope(self) -> Environment:
        """Return a child frame whose declarations and relation facts are local."""
        return Environment(context=self.context.copy(), parent=self)

    def bind_runtime_name(self, source_name: Symbol, runtime_name: Symbol) -> None:
        """Bind one source-level name to its compiled runtime storage name."""
        self.runtime_names[source_name] = runtime_name

    def runtime_name_for(self, source_name: Symbol) -> Symbol:
        """Return the runtime storage name visible for a source-level symbol."""
        if source_name in self.runtime_names:
            return self.runtime_names[source_name]
        if self.parent is not None:
            return self.parent.runtime_name_for(source_name)
        return source_name

    def define_object(
        self,
        name: Symbol,
        attributes: tuple[ObjectAttribute, ...] = (),
        *,
        generics: tuple[Symbol, ...] = (),
        generic_variance: tuple[Variance, ...] = (),
    ) -> None:
        """Register or replace an object type visible in this environment."""
        seen: set[Symbol] = set()
        for attribute in attributes:
            if attribute.name in seen:
                raise ValueError(
                    f"object {name!r} declares attribute {attribute.name!r} twice"
                )
            seen.add(attribute.name)
        variances = _generic_variance(generics, generic_variance)
        self.objects[name] = ObjectDefinition(name, generics, variances, attributes)
        self.context.set_generic_variance(name, variances)

    def lookup_object(self, name: Symbol) -> ObjectDefinition | None:
        """Return an object definition, if one exists in scope."""
        if name in self.objects:
            return self.objects[name]
        if self.parent is not None:
            return self.parent.lookup_object(name)
        return None

    def define_constructor_metadata(
        self,
        name: Symbol,
        fields: tuple[ObjectAttribute, ...],
        *,
        defaults: frozenset[Symbol] = frozenset(),
        generic_constraints: tuple[GenericConstraint, ...] = (),
    ) -> None:
        """Register runtime constructor metadata without synthesizing an overload."""
        self.constructors[name] = ConstructorDefinition(
            name,
            fields,
            defaults,
            generic_constraints,
        )

    def define_constructor(
        self,
        name: Symbol,
        fields: tuple[ObjectAttribute, ...],
        *,
        defaults: frozenset[Symbol] = frozenset(),
        result_type: Type | None = None,
        generic_constraints: tuple[GenericConstraint, ...] = (),
    ) -> None:
        """Register constructor metadata and synthesize its field-order overload."""
        self.define_constructor_metadata(
            name,
            fields,
            defaults=defaults,
            generic_constraints=generic_constraints,
        )
        params = tuple(field.typ for field in fields if field.name not in defaults)
        self.define_overload(
            name,
            Overload(params, (result_type or NominalType(name),), generic_constraints),
        )

    def lookup_constructor(self, name: Symbol) -> ConstructorDefinition | None:
        """Return constructor metadata, if visible."""
        if name in self.constructors:
            return self.constructors[name]
        if self.parent is not None:
            return self.parent.lookup_constructor(name)
        return None

    def define_trait(
        self,
        name: Symbol,
        *,
        generics: tuple[Symbol, ...] = (),
        generic_variance: tuple[Variance, ...] = (),
        requirements: tuple[TraitRequirement, ...] = (),
    ) -> None:
        """Register a trait definition."""
        variances = _generic_variance(generics, generic_variance)
        self.traits[name] = TraitDefinition(name, generics, variances, requirements)
        self.context.set_generic_variance(name, variances)

    def lookup_trait(self, name: Symbol) -> TraitDefinition | None:
        """Return a trait definition, if visible."""
        if name in self.traits:
            return self.traits[name]
        if self.parent is not None:
            return self.parent.lookup_trait(name)
        return None

    def define_variant(
        self,
        name: Symbol,
        members: tuple[Symbol, ...],
        *,
        generics: tuple[Symbol, ...] = (),
        generic_variance: tuple[Variance, ...] = (),
        requirements: tuple[TraitRequirement, ...] = (),
    ) -> None:
        """Register a closed variant and its members."""
        variances = _generic_variance(generics, generic_variance)
        self.variants[name] = VariantDefinition(
            name,
            generics,
            variances,
            members,
            requirements,
        )
        self.context.set_generic_variance(name, variances)
        for member in members:
            self.add_variant_member(member, name)

    def lookup_variant(self, name: Symbol) -> VariantDefinition | None:
        """Return a variant definition, if visible."""
        if name in self.variants:
            return self.variants[name]
        if self.parent is not None:
            return self.parent.lookup_variant(name)
        return None

    def define_enum(
        self,
        name: Symbol,
        members: tuple[EnumMemberDefinition, ...],
        *,
        value_type: Type | None = None,
    ) -> None:
        """Register an enum and niladic overloads for its members."""
        self.enums[name] = EnumDefinition(name, value_type, members)
        for member in members:
            self.define_overload(member.name, Overload((), (NominalType(name),)))
            if member.typ is not None and member.has_value:
                self.define_overload(
                    Symbol("value", (*member.name.namespace, member.name.text)),
                    Overload((), (member.typ,)),
                )

    def lookup_enum(self, name: Symbol) -> EnumDefinition | None:
        """Return an enum definition, if visible."""
        if name in self.enums:
            return self.enums[name]
        if self.parent is not None:
            return self.parent.lookup_enum(name)
        return None

    def object_exists(self, name: Symbol) -> bool:
        """Return whether an object type exists in scope."""
        return self.lookup_object(name) is not None

    def lookup_attribute(
        self,
        object_name: Symbol,
        attribute_name: Symbol,
    ) -> Type | None:
        """Return the declared type of ``object_name.attribute_name``."""
        definition = self.lookup_object(object_name)
        if definition is None:
            return None
        return definition.attribute_type(attribute_name)

    def lookup_attribute_definition(
        self,
        object_name: Symbol,
        attribute_name: Symbol,
    ) -> ObjectAttribute | None:
        """Return the declared attribute metadata of ``object_name.attribute``."""
        definition = self.lookup_object(object_name)
        if definition is None:
            return None
        return definition.attribute(attribute_name)

    def has_attribute(self, object_name: Symbol, attribute_name: Symbol) -> bool:
        """Return whether an object declares the requested attribute."""
        return self.lookup_attribute(object_name, attribute_name) is not None

    def define_overload(
        self,
        name: Symbol,
        overload: Overload,
        *,
        object_friendly: bool = False,
    ) -> None:
        """Append one overload to a named overload set."""
        candidates = self.overloads.setdefault(name, [])
        if candidates:
            fixed_candidates = tuple(
                candidate
                for candidate in candidates
                if not _is_call_site_checked(candidate)
            )
            expected_arity = len(candidates[0].params)
            if len(overload.params) != expected_arity:
                raise ValueError(
                    f"overloads for {name!r} must all take {expected_arity} "
                    f"inputs, got {len(overload.params)}"
                )
            if (
                fixed_candidates
                and not _is_call_site_checked(overload)
                and len(overload.returns) != len(fixed_candidates[0].returns)
            ):
                expected_returns = len(fixed_candidates[0].returns)
                raise ValueError(
                    f"overloads for {name!r} must all return {expected_returns} "
                    f"values, got {len(overload.returns)}"
                )
        overload_index = len(candidates)
        candidates.append(overload)
        if object_friendly:
            self.object_friendly_overloads.setdefault(name, set()).add(overload_index)
        self.context.define_structural_overload(name, overload)

    def overload_is_object_friendly(self, name: Symbol, index: int) -> bool:
        """Return whether one visible overload is an unqualified friendly one."""
        if name.text.startswith("*::"):
            if self.parent is None:
                builtin_name = Symbol(name.text.removeprefix("*::"))
                return index in self.object_friendly_overloads.get(
                    builtin_name,
                    set(),
                )
            return self.parent.overload_is_object_friendly(name, index)
        local = tuple(self.overloads.get(name, ()))
        if local or self.parent is None:
            return index in self.object_friendly_overloads.get(name, set())
        return self.parent.overload_is_object_friendly(name, index)

    def non_object_friendly_overload_index(
        self,
        name: Symbol,
        overload: Overload,
    ) -> int | None:
        """Return the index of a matching non-friendly overload, if present."""
        for index, candidate in enumerate(self.overloads_for(name)):
            if candidate == overload and not self.overload_is_object_friendly(
                name,
                index,
            ):
                return index
        return None

    def has_non_object_friendly_overload(
        self,
        name: Symbol,
        overload: Overload,
    ) -> bool:
        """Return whether a matching visible overload is not a friendly default."""
        return self.non_object_friendly_overload_index(name, overload) is not None

    def has_local_non_object_friendly_overload(
        self,
        name: Symbol,
        overload: Overload,
    ) -> bool:
        """Return whether this scope already defines the requested overload.

        User definitions live in a child scope above built-ins and imports. A
        same-shaped local definition must therefore be registered so it shadows
        the parent callable at both analysis and runtime.
        """
        friendly = self.object_friendly_overloads.get(name, set())
        return any(
            candidate == overload and index not in friendly
            for index, candidate in enumerate(self.overloads.get(name, ()))
        )

    def overloads_for(self, name: Symbol) -> tuple[Overload, ...]:
        """Return the overload candidates registered for ``name``."""
        if name.text.startswith("*::"):
            builtin_name = Symbol(name.text.removeprefix("*::"))
            if self.parent is None:
                return tuple(self.overloads.get(builtin_name, ()))
            return self.parent.overloads_for(name)
        local = tuple(self.overloads.get(name, ()))
        if local or self.parent is None:
            return local
        return self.parent.overloads_for(name)

    def visible_overload_names(self) -> tuple[Symbol, ...]:
        """Return callable names visible through lexical shadowing."""
        names: dict[Symbol, None] = {}
        current: Environment | None = self
        while current is not None:
            for name in current.overloads:
                names.setdefault(name, None)
            current = current.parent
        return tuple(sorted(names, key=str))

    def value_type(self, name: Symbol) -> Type | None:
        """Return the overload-set type of a named callable value."""
        overloads = self.overloads_for(name)
        if overloads:
            return OverloadSetType(overloads)
        return None

    def add_trait_impl(self, type_name: Symbol, trait_name: Symbol) -> None:
        """Record that a concrete type implements a trait."""
        self.context.trait_impls.setdefault(type_name, set()).add(trait_name)

    def add_trait_parent(self, trait_name: Symbol, parent_name: Symbol) -> None:
        """Record that one trait implies another trait."""
        self.context.trait_parents.setdefault(trait_name, set()).add(parent_name)

    def add_variant_member(self, member_name: Symbol, variant_name: Symbol) -> None:
        """Record that a nominal type belongs to a variant."""
        self.context.variant_members[member_name] = variant_name

    def define_tag(self, tag: str | Symbol, kind: TagKind) -> None:
        """Register a data tag declaration visible in this environment."""
        name = _tag_symbol(tag)
        self.data_tags[name] = DataTagDefinition(name, kind)
        self.context.define_tag(name, kind)

    def lookup_tag(self, tag: str | Symbol) -> DataTagDefinition | None:
        """Return a data tag declaration, if visible."""
        name = _tag_symbol(tag)
        if name in self.data_tags:
            return self.data_tags[name]
        if self.parent is not None:
            return self.parent.lookup_tag(name)
        return None

    def define_element_tag(
        self,
        tag: str | Symbol,
        kind: ElementTagKind,
    ) -> None:
        """Register an element tag declaration visible in this environment."""
        name = _tag_symbol(tag)
        self.element_tags[name] = ElementTagDefinition(name, kind)

    def lookup_element_tag(self, tag: str | Symbol) -> ElementTagDefinition | None:
        """Return an element tag declaration, if visible."""
        name = _tag_symbol(tag)
        if name in self.element_tags:
            return self.element_tags[name]
        if self.parent is not None:
            return self.parent.lookup_element_tag(name)
        return None

    def add_property_element_tag(self, tag: str | Symbol) -> None:
        """Record a user-attachable element tag."""
        self.define_element_tag(tag, ElementTagKind.PROPERTY)

    def add_companion_element_tag(self, tag: str | Symbol) -> None:
        """Record a system-attached companion element tag."""
        self.define_element_tag(tag, ElementTagKind.COMPANION)

    def add_disjoint_element_tags(
        self,
        left: str | Symbol,
        right: str | Symbol,
    ) -> None:
        """Record that two element tags cannot appear together."""
        left_name = _tag_symbol(left)
        right_name = _tag_symbol(right)
        self.disjoint_element_tags.setdefault(left_name, set()).add(right_name)
        self.disjoint_element_tags.setdefault(right_name, set()).add(left_name)

    def element_tag_disjoints(self, tag: str | Symbol) -> frozenset[Symbol]:
        """Return element tags declared disjoint with ``tag``."""
        name = _tag_symbol(tag)
        local = self.disjoint_element_tags.get(name, set())
        parent = (
            self.parent.element_tag_disjoints(name)
            if self.parent is not None
            else frozenset()
        )
        return frozenset((*parent, *local))

    def add_disjoint_data_element_tags(
        self,
        data_tag: str | Symbol,
        element_tag: str | Symbol,
    ) -> None:
        """Record that tagged data cannot be passed to the element effect."""
        data_name = _tag_symbol(data_tag)
        element_name = _tag_symbol(element_tag)
        self.disjoint_data_element_tags.setdefault(data_name, set()).add(element_name)

    def data_tag_element_disjoints(self, tag: str | Symbol) -> frozenset[Symbol]:
        """Return element tags disjoint with a data tag."""
        name = _tag_symbol(tag)
        local = self.disjoint_data_element_tags.get(name, set())
        parent = (
            self.parent.data_tag_element_disjoints(name)
            if self.parent is not None
            else frozenset()
        )
        return frozenset((*parent, *local))

    def element_tag_data_disjoints(self, tag: str | Symbol) -> frozenset[Symbol]:
        """Return data tags disjoint with an element tag."""
        name = _tag_symbol(tag)
        local = {
            data_tag
            for data_tag, element_tags in self.disjoint_data_element_tags.items()
            if name in element_tags
        }
        parent = (
            self.parent.element_tag_data_disjoints(name)
            if self.parent is not None
            else frozenset()
        )
        return frozenset((*parent, *local))

    def add_unit_tag(self, tag: str | Symbol) -> None:
        """Record a tag that cannot be silently erased."""
        self.define_tag(tag, TagKind.UNIT)

    def add_constructed_tag(self, tag: str | Symbol) -> None:
        """Record a sticky constructed data tag."""
        self.define_tag(tag, TagKind.CONSTRUCTED)

    def add_computed_tag(self, tag: str | Symbol) -> None:
        """Record a non-sticky computed data tag."""
        self.define_tag(tag, TagKind.COMPUTED)

    def add_variant_tag(self, tag: str | Symbol, parent: str | Symbol) -> None:
        """Record a runtime variant tag with a computed parent tag."""
        name = _tag_symbol(tag)
        parent_name = _tag_symbol(parent)
        self.data_tags[name] = DataTagDefinition(name, TagKind.VARIANT)
        self.data_tags.setdefault(
            parent_name,
            DataTagDefinition(parent_name, TagKind.COMPUTED),
        )
        self.tag_parents[name] = parent_name
        self.context.define_variant_tag(name, parent_name)

    def add_disjoint_tags(self, tag: str | Symbol, other: str | Symbol) -> None:
        """Record two data tags that remove each other."""
        name = _tag_symbol(tag)
        other_name = _tag_symbol(other)
        self.disjoint_tags.setdefault(name, set()).add(other_name)
        self.disjoint_tags.setdefault(other_name, set()).add(name)
        self.context.add_disjoint_tags(name, other_name)

    def define_tag_overlay(
        self,
        tag: str | Symbol,
        element: Symbol,
        overload: Overload,
        *,
        public: bool = False,
    ) -> None:
        """Register a tag overlay signature for an existing element."""
        definition = TagOverlayDefinition(_tag_symbol(tag), element, overload, public)
        self.tag_overlays.setdefault(element, []).append(definition)

    def overlays_for(self, element: Symbol) -> tuple[TagOverlayDefinition, ...]:
        """Return overlay signatures visible for ``element``."""
        local = tuple(self.tag_overlays.get(element, ()))
        parent = self.parent.overlays_for(element) if self.parent is not None else ()
        return (*parent, *local)

    def define_tag_attached_element(self, tag: str | Symbol, element: Symbol) -> None:
        """Record that importing ``tag`` should also import ``element``."""
        self.tag_attached_elements.setdefault(_tag_symbol(tag), set()).add(element)

    def attached_elements_for_tag(self, tag: str | Symbol) -> frozenset[Symbol]:
        """Return elements attached to a tag declaration."""
        name = _tag_symbol(tag)
        local = self.tag_attached_elements.get(name, set())
        parent = (
            self.parent.attached_elements_for_tag(name)
            if self.parent is not None
            else frozenset()
        )
        return frozenset((*parent, *local))

    def set_tag_validator_static_result(
        self,
        validator: Symbol,
        overload_index: int,
        result: bool,
    ) -> None:
        """Remember that one validator overload is statically constant."""
        self.tag_validator_static_results.setdefault(validator, {})[
            overload_index
        ] = result

    def tag_validator_static_result(
        self,
        validator: Symbol,
        overload_index: int,
    ) -> bool | None:
        """Return a statically known validator result, if one exists."""
        if overload_index in self.tag_validator_static_results.get(validator, {}):
            return self.tag_validator_static_results[validator][overload_index]
        if self.parent is not None:
            return self.parent.tag_validator_static_result(validator, overload_index)
        return None

    def apply(
        self,
        name: Symbol,
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


def _is_call_site_checked(overload: Overload) -> bool:
    """Return whether the value is call site checked."""
    return any(_is_call_site_checked_type(param) for param in overload.params)


def _is_call_site_checked_type(typ: Type) -> bool:
    """Return whether the value is call site checked type."""
    if isinstance(typ, FunctionType) and typ.params is None and typ.returns is None:
        return True
    if isinstance(typ, VariadicTupleType):
        return True
    return False


def _tag_symbol(name: str | Symbol) -> Symbol:
    """Normalize parser-facing tag names into symbol-table keys."""
    return name if isinstance(name, Symbol) else Symbol(name)


def _generic_variance(
    generics: tuple[Symbol, ...],
    variances: tuple[Variance, ...],
) -> tuple[Variance, ...]:
    """Compute generic variance in the global type environment."""
    if len(variances) == len(generics):
        return variances
    return (Variance.INVARIANT,) * len(generics)
