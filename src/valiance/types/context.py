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
    data_tags: dict[str, TagKind] = field(default_factory=dict[str, TagKind])
    tag_parents: dict[str, str] = field(default_factory=dict[str, str])
    disjoint_tags: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    unit_tags: set[str] = field(default_factory=set[str])

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

    def define_tag(self, name: str, kind: TagKind) -> None:
        """Register a data tag category."""
        self.data_tags[name] = kind
        if kind is TagKind.UNIT:
            self.unit_tags.add(name)

    def define_variant_tag(self, name: str, parent: str) -> None:
        """Register a runtime variant tag and its computed parent."""
        self.data_tags[name] = TagKind.VARIANT
        self.tag_parents[name] = parent
        self.data_tags.setdefault(parent, TagKind.COMPUTED)

    def add_disjoint_tags(self, name: str, other: str) -> None:
        """Record that applying one tag removes the other."""
        self.disjoint_tags.setdefault(name, set()).add(other)
        self.disjoint_tags.setdefault(other, set()).add(name)

    def tag_kind(self, name: str) -> TagKind:
        """Return a tag's declared kind, defaulting to computed."""
        return self.data_tags.get(name, TagKind.COMPUTED)

    def is_constructed_like_tag(self, name: str) -> bool:
        """Return whether a tag should stick through ordinary operations."""
        return self.tag_kind(name) in {TagKind.CONSTRUCTED, TagKind.UNIT}
