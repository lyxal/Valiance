"""Stable declaration-level products for incremental semantic analysis.

The records in this module are deliberately independent from live analyser state.
They form the persistence and invalidation boundary used by later analyser hooks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable, Mapping, Sequence


class DeclarationKind(StrEnum):
    """Identify a persistent declaration namespace."""

    DEFINE = "define"
    OBJECT = "object"
    TRAIT = "trait"
    VARIANT = "variant"
    ENUM = "enum"
    TAG = "tag"
    OVERLAY = "overlay"
    IMPORT = "import"
    EXECUTABLE_REGION = "executable-region"


@dataclass(frozen=True, order=True)
class DeclarationIdentity:
    """Name one source declaration without depending on offsets or Python identity."""

    package: str
    module: str
    kind: DeclarationKind
    name: str
    owner: str = ""
    discriminator: str = ""

    def canonical_key(self) -> str:
        """Return the stable textual key used by indexes and fingerprints."""
        return "/".join(
            (self.package, self.module, self.kind.value, self.owner, self.name, self.discriminator)
        )


class DependencyKind(StrEnum):
    """Classify a semantic read at the precision needed for invalidation."""

    NAME_BINDING = "name-binding"
    VISIBLE_OVERLOAD_SET = "visible-overload-set"
    SELECTED_OVERLOAD = "selected-overload"
    FUNCTION_CONTRACT = "function-contract"
    OBJECT_SHAPE = "object-shape"
    CONSTRUCTOR_SHAPE = "constructor-shape"
    TRAIT_REQUIREMENTS = "trait-requirements"
    BEHAVIOUR_PROVIDER = "behaviour-provider"
    VARIANT_MEMBERSHIP = "variant-membership"
    GENERIC_VARIANCE = "generic-variance"
    DATA_TAG = "data-tag"
    TAG_OVERLAY = "tag-overlay"
    TAG_VALIDATOR = "tag-validator"
    ELEMENT_TAG_CONTRACT = "element-tag-contract"
    STATIC_VALUE = "static-value"
    IMPORTED_RUNTIME = "imported-runtime"
    ANNOTATION = "annotation"
    LIFECYCLE_EFFECTS = "lifecycle-effects"
    EXECUTABLE_STATE = "executable-state"


@dataclass(frozen=True, order=True)
class SemanticDependency:
    """Record one consumed semantic fact and the fingerprint observed by a consumer."""

    kind: DependencyKind
    subject: str
    fingerprint: str

    @classmethod
    def declaration(
        cls,
        kind: DependencyKind,
        identity: DeclarationIdentity,
        fingerprint: str,
    ) -> "SemanticDependency":
        """Construct a dependency on a declaration-owned semantic fact."""
        return cls(kind, identity.canonical_key(), fingerprint)


@dataclass(frozen=True)
class PublishedFact:
    """Represent one restorable environment fact emitted by a declaration."""

    kind: str
    key: str
    fingerprint: str
    payload: object | None = None


@dataclass(frozen=True)
class SemanticProduct:
    """Persist the stable result of analysing one declaration or executable region."""

    identity: DeclarationIdentity
    syntax_fingerprint: str
    header_fingerprint: str
    semantic_input_fingerprint: str
    semantic_output_fingerprint: str
    implementation_fingerprint: str
    dependencies: tuple[SemanticDependency, ...] = ()
    published_facts: tuple[PublishedFact, ...] = ()
    typed_payload: object | None = None
    diagnostics: tuple[object, ...] = ()
    lints: tuple[object, ...] = ()
    incoming_state_fingerprint: str = ""
    outgoing_state_fingerprint: str = ""

    def reusable(
        self,
        *,
        syntax_fingerprint: str,
        available_facts: Mapping[tuple[DependencyKind, str], str],
        incoming_state_fingerprint: str = "",
        diagnostics_policy_fingerprint: str = "",
        previous_diagnostics_policy_fingerprint: str = "",
    ) -> bool:
        """Return whether this product can be restored without body analysis."""
        if self.syntax_fingerprint != syntax_fingerprint:
            return False
        if self.incoming_state_fingerprint != incoming_state_fingerprint:
            return False
        if diagnostics_policy_fingerprint != previous_diagnostics_policy_fingerprint:
            return False
        return all(
            available_facts.get((dependency.kind, dependency.subject))
            == dependency.fingerprint
            for dependency in self.dependencies
        )


@dataclass(frozen=True)
class InvalidationResult:
    """Describe products invalidated directly or through changed outputs."""

    invalidated: frozenset[DeclarationIdentity]
    reusable: frozenset[DeclarationIdentity]
    deleted: frozenset[DeclarationIdentity]
    reasons: Mapping[DeclarationIdentity, tuple[str, ...]] = field(default_factory=dict)


def canonical_fingerprint(value: object) -> str:
    """Hash JSON-compatible semantic data with deterministic ordering and type tags."""

    def normalize(item: object) -> object:
        """Turn supported immutable compiler data into canonical JSON values."""
        if item is None or isinstance(item, (bool, int, float, str)):
            return item
        if isinstance(item, bytes):
            return {"bytes": item.hex()}
        if isinstance(item, StrEnum):
            return {"enum": f"{type(item).__module__}.{type(item).__qualname__}", "value": item.value}
        if isinstance(item, DeclarationIdentity):
            return {"declaration": item.canonical_key()}
        if isinstance(item, Mapping):
            pairs = [(normalize(key), normalize(value)) for key, value in item.items()]
            pairs.sort(key=lambda pair: json.dumps(pair[0], sort_keys=True, separators=(",", ":")))
            return {"mapping": pairs}
        if isinstance(item, (set, frozenset)):
            values = [normalize(value) for value in item]
            values.sort(key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
            return {"set": values}
        if isinstance(item, tuple):
            return {"tuple": [normalize(value) for value in item]}
        if isinstance(item, list):
            return {"list": [normalize(value) for value in item]}
        if hasattr(item, "__dataclass_fields__"):
            names = tuple(item.__dataclass_fields__)
            return {
                "record": f"{type(item).__module__}.{type(item).__qualname__}",
                "fields": [(name, normalize(getattr(item, name))) for name in names],
            }
        raise TypeError(f"unsupported fingerprint value: {type(item).__name__}")

    encoded = json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def semantic_input_fingerprint(dependencies: Iterable[SemanticDependency]) -> str:
    """Hash a dependency set independently from discovery order."""
    ordered = tuple(sorted(dependencies))
    return canonical_fingerprint(ordered)


def published_fact_map(products: Iterable[SemanticProduct]) -> dict[tuple[DependencyKind, str], str]:
    """Build the dependency lookup exposed by currently published products."""
    result: dict[tuple[DependencyKind, str], str] = {}
    for product in products:
        subject = product.identity.canonical_key()
        for dependency_kind in DependencyKind:
            result[(dependency_kind, subject)] = product.semantic_output_fingerprint
        for fact in product.published_facts:
            try:
                kind = DependencyKind(fact.kind)
            except ValueError:
                continue
            result[(kind, fact.key)] = fact.fingerprint
    return result


def invalidate_products(
    previous: Mapping[DeclarationIdentity, SemanticProduct],
    current_syntax: Mapping[DeclarationIdentity, str],
    *,
    changed_fact_fingerprints: Mapping[tuple[DependencyKind, str], str] | None = None,
    incoming_states: Mapping[DeclarationIdentity, str] | None = None,
) -> InvalidationResult:
    """Compute the least invalidation set implied by syntax, deletion, and semantic reads.

    Output changes are propagated by callers after reanalysis by invoking this function
    again with the changed fact fingerprints. This prevents body-only edits with stable
    contracts from invalidating semantic consumers.
    """
    facts = dict(published_fact_map(previous.values()))
    if changed_fact_fingerprints:
        facts.update(changed_fact_fingerprints)
    incoming_states = incoming_states or {}
    invalidated: set[DeclarationIdentity] = set()
    deleted = set(previous).difference(current_syntax)
    reasons: dict[DeclarationIdentity, list[str]] = {identity: ["declaration deleted"] for identity in deleted}

    for identity, syntax in current_syntax.items():
        product = previous.get(identity)
        if product is None:
            invalidated.add(identity)
            reasons.setdefault(identity, []).append("new declaration")
            continue
        if product.syntax_fingerprint != syntax:
            invalidated.add(identity)
            reasons.setdefault(identity, []).append("syntax changed")
            continue
        expected_state = incoming_states.get(identity, "")
        if product.incoming_state_fingerprint != expected_state:
            invalidated.add(identity)
            reasons.setdefault(identity, []).append("incoming executable state changed")
            continue
        for dependency in product.dependencies:
            if facts.get((dependency.kind, dependency.subject)) != dependency.fingerprint:
                invalidated.add(identity)
                reasons.setdefault(identity, []).append(
                    f"{dependency.kind.value} changed: {dependency.subject}"
                )
                break

    reusable = set(current_syntax).difference(invalidated)
    return InvalidationResult(
        frozenset(invalidated),
        frozenset(reusable),
        frozenset(deleted),
        {identity: tuple(values) for identity, values in reasons.items()},
    )


class SemanticProductStore:
    """Hold declaration products and expose explicit restore/publication operations."""

    def __init__(self, products: Iterable[SemanticProduct] = ()) -> None:
        """Create an in-memory semantic product index."""
        self._products = {product.identity: product for product in products}

    def get(self, identity: DeclarationIdentity) -> SemanticProduct | None:
        """Return the product for one stable declaration identity."""
        return self._products.get(identity)

    def publish(self, product: SemanticProduct) -> None:
        """Atomically replace one declaration product in this index."""
        self._products[product.identity] = product

    def remove(self, identity: DeclarationIdentity) -> SemanticProduct | None:
        """Remove a deleted declaration and return its former product."""
        return self._products.pop(identity, None)

    def snapshot(self) -> tuple[SemanticProduct, ...]:
        """Return products in stable declaration-identity order."""
        return tuple(self._products[key] for key in sorted(self._products))

    def plan(
        self,
        current_syntax: Mapping[DeclarationIdentity, str],
        *,
        changed_fact_fingerprints: Mapping[tuple[DependencyKind, str], str] | None = None,
        incoming_states: Mapping[DeclarationIdentity, str] | None = None,
    ) -> InvalidationResult:
        """Plan declaration reuse against the current syntax and semantic facts."""
        return invalidate_products(
            self._products,
            current_syntax,
            changed_fact_fingerprints=changed_fact_fingerprints,
            incoming_states=incoming_states,
        )
