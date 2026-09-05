"""Tests for declaration-level semantic product invalidation."""

from __future__ import annotations

import unittest

from valiance.incremental import (
    DeclarationIdentity,
    DeclarationKind,
    DependencyKind,
    SemanticDependency,
    SemanticProduct,
    SemanticProductStore,
    canonical_fingerprint,
    semantic_input_fingerprint,
)


class IncrementalSemanticTests(unittest.TestCase):
    """Exercise stable identities, fingerprints, and precise invalidation."""

    def identity(self, name: str) -> DeclarationIdentity:
        """Create a definition identity in one test module."""
        return DeclarationIdentity("pkg", "main", DeclarationKind.DEFINE, name)

    def product(
        self,
        identity: DeclarationIdentity,
        syntax: str,
        output: str,
        dependencies: tuple[SemanticDependency, ...] = (),
    ) -> SemanticProduct:
        """Create a compact valid product for one test declaration."""
        return SemanticProduct(
            identity,
            syntax,
            "header",
            semantic_input_fingerprint(dependencies),
            output,
            "implementation",
            dependencies,
        )

    def test_fingerprints_are_order_independent_for_maps_and_sets(self) -> None:
        """Canonical hashing ignores insertion order for unordered values."""
        self.assertEqual(
            canonical_fingerprint({"a": {3, 2, 1}, "b": 2}),
            canonical_fingerprint({"b": 2, "a": {1, 3, 2}}),
        )

    def test_body_edit_does_not_eagerly_invalidate_consumer(self) -> None:
        """A syntax edit invalidates its body but not consumers until output changes."""
        provider = self.identity("provider")
        consumer = self.identity("consumer")
        dependency = SemanticDependency.declaration(
            DependencyKind.FUNCTION_CONTRACT, provider, "contract-v1"
        )
        store = SemanticProductStore(
            (
                self.product(provider, "syntax-v1", "contract-v1"),
                self.product(consumer, "consumer-syntax", "consumer-contract", (dependency,)),
            )
        )
        plan = store.plan({provider: "syntax-v2", consumer: "consumer-syntax"})
        self.assertEqual(plan.invalidated, frozenset({provider}))
        self.assertIn(consumer, plan.reusable)

    def test_changed_semantic_output_invalidates_recorded_consumer(self) -> None:
        """A changed contract invalidates only declarations that consumed that fact."""
        provider = self.identity("provider")
        consumer = self.identity("consumer")
        unrelated = self.identity("unrelated")
        dependency = SemanticDependency.declaration(
            DependencyKind.FUNCTION_CONTRACT, provider, "contract-v1"
        )
        store = SemanticProductStore(
            (
                self.product(provider, "provider", "contract-v1"),
                self.product(consumer, "consumer", "consumer-output", (dependency,)),
                self.product(unrelated, "unrelated", "unrelated-output"),
            )
        )
        changed = {
            (DependencyKind.FUNCTION_CONTRACT, provider.canonical_key()): "contract-v2"
        }
        plan = store.plan(
            {provider: "provider", consumer: "consumer", unrelated: "unrelated"},
            changed_fact_fingerprints=changed,
        )
        self.assertEqual(plan.invalidated, frozenset({consumer}))
        self.assertIn(unrelated, plan.reusable)

    def test_deletion_is_reported_separately(self) -> None:
        """Deleted declarations are removed from publication rather than reanalysed."""
        deleted = self.identity("deleted")
        plan = SemanticProductStore((self.product(deleted, "syntax", "output"),)).plan({})
        self.assertEqual(plan.deleted, frozenset({deleted}))
        self.assertFalse(plan.invalidated)

    def test_executable_region_requires_same_incoming_state(self) -> None:
        """Top-level executable regions cannot reuse across changed stack state."""
        identity = DeclarationIdentity(
            "pkg", "main", DeclarationKind.EXECUTABLE_REGION, "region", discriminator="0"
        )
        product = SemanticProduct(
            identity,
            "syntax",
            "header",
            "inputs",
            "outputs",
            "implementation",
            incoming_state_fingerprint="stack-v1",
            outgoing_state_fingerprint="stack-v2",
        )
        plan = SemanticProductStore((product,)).plan(
            {identity: "syntax"}, incoming_states={identity: "different-stack"}
        )
        self.assertEqual(plan.invalidated, frozenset({identity}))


if __name__ == "__main__":
    unittest.main()
