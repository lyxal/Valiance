"""Type relationship facts used by relation checks."""

from __future__ import annotations

from dataclasses import dataclass, field

from valiance.symbols import Symbol


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
