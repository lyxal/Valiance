from __future__ import annotations

"""Type relationship facts used by relation checks."""

from dataclasses import dataclass, field


@dataclass
class Context:
    """Mutable registry of relationships needed by type checks."""

    # The parser/symbol-table layer owns declarations. Context only stores the
    # relationships that the relation functions need to answer questions.
    trait_impls: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    trait_parents: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    variant_members: dict[str, str] = field(default_factory=dict[str, str])
    unit_tags: set[str] = field(default_factory=set[str])

    def implements(self, type_name: str, trait_name: str) -> bool:
        """Return whether a nominal type implements a trait, following parents."""
        seen: set[str] = set()
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
