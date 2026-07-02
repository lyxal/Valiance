"""Type relationship facts used by relation checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from valiance.symbols import Symbol


class TagKind(Enum):
    """Static data-tag categories used by tag propagation."""

    CONSTRUCTED = auto()
    COMPUTED = auto()
    UNIT = auto()
    VARIANT = auto()


class Variance(Enum):
    """How one nominal generic argument participates in subtyping."""

    INVARIANT = auto()
    COVARIANT = auto()
    CONTRAVARIANT = auto()


@dataclass
class Context:
    """Mutable registry of relationships needed by type checks."""

    # The parser/symbol-table layer owns declarations. Context only stores the
    # relationships that the relation functions need to answer questions.
    trait_impls: dict[Symbol, set[Symbol]] = field(
        default_factory=dict[Symbol, set[Symbol]]
    )
    trait_parents: dict[Symbol, set[Symbol]] = field(
        default_factory=dict[Symbol, set[Symbol]]
    )
    variant_members: dict[Symbol, Symbol] = field(
        default_factory=dict[Symbol, Symbol]
    )
    data_tags: dict[Symbol, TagKind] = field(default_factory=dict[Symbol, TagKind])
    tag_parents: dict[Symbol, Symbol] = field(default_factory=dict[Symbol, Symbol])
    disjoint_tags: dict[Symbol, set[Symbol]] = field(
        default_factory=dict[Symbol, set[Symbol]]
    )
    generic_variance: dict[Symbol, tuple[Variance, ...]] = field(
        default_factory=dict[Symbol, tuple[Variance, ...]]
    )

    def implements(self, type_name: Symbol, trait_name: Symbol) -> bool:
        """Return whether a nominal type implements a trait, following parents."""
        seen: set[Symbol] = set()
        pending = list(self.trait_impls.get(type_name, set()))
        if type_name == trait_name:
            return True
        while pending:
            trait = pending.pop()
            if trait in seen:
                continue
            if trait == trait_name:
                return True
            seen.add(trait)
            pending.extend(self.trait_parents.get(trait, set()))
        return False

    def define_tag(self, name: str | Symbol, kind: TagKind) -> None:
        """Register a data tag category."""
        self.data_tags[_tag_symbol(name)] = kind

    def define_variant_tag(self, name: str | Symbol, parent: str | Symbol) -> None:
        """Register a runtime variant tag and its computed parent."""
        tag = _tag_symbol(name)
        parent_tag = _tag_symbol(parent)
        self.data_tags[tag] = TagKind.VARIANT
        self.tag_parents[tag] = parent_tag
        self.data_tags.setdefault(parent_tag, TagKind.COMPUTED)

    def add_disjoint_tags(self, name: str | Symbol, other: str | Symbol) -> None:
        """Record that applying one tag removes the other."""
        tag = _tag_symbol(name)
        other_tag = _tag_symbol(other)
        self.disjoint_tags.setdefault(tag, set()).add(other_tag)
        self.disjoint_tags.setdefault(other_tag, set()).add(tag)

    def tag_kind(self, name: str | Symbol) -> TagKind:
        """Return a tag's declared kind, defaulting to computed."""
        return self.data_tags.get(_tag_symbol(name), TagKind.COMPUTED)

    def is_constructed_like_tag(self, name: str | Symbol) -> bool:
        """Return whether a tag should stick through ordinary operations."""
        return self.tag_kind(name) in {TagKind.CONSTRUCTED, TagKind.UNIT}

    def tag_parent(self, name: str | Symbol) -> Symbol | None:
        """Return the computed parent of a variant tag, if declared."""
        return self.tag_parents.get(_tag_symbol(name))

    def tag_disjoints(self, name: str | Symbol) -> set[Symbol]:
        """Return tags disjoint with ``name``."""
        return self.disjoint_tags.get(_tag_symbol(name), set())

    def is_unit_tag(self, name: str | Symbol) -> bool:
        """Return whether a tag has unit semantics."""
        return self.tag_kind(name) is TagKind.UNIT

    def set_generic_variance(
        self,
        name: Symbol,
        variances: tuple[Variance, ...],
    ) -> None:
        """Record declaration-site variance for a nominal constructor."""
        self.generic_variance[name] = variances

    def variance_for(self, name: Symbol, arity: int) -> tuple[Variance, ...]:
        """Return declared variance, defaulting unknown constructors invariant."""
        variances = self.generic_variance.get(name, ())
        if len(variances) != arity:
            return (Variance.INVARIANT,) * arity
        return variances


def _tag_symbol(name: str | Symbol) -> Symbol:
    """Normalize parser-facing tag names into symbol-table keys."""
    return name if isinstance(name, Symbol) else Symbol(name)
