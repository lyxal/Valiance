"""Focused lifecycle contract validation."""

from __future__ import annotations


from valiance.analysis.contracts.destructor_borrows import (
    destructor_borrow_violations,
)
from valiance.asts import ObjectNode

from ..support import analysis_utils as _utils


class _LifecycleContracts:
    """Own lifecycle contract operations."""

    def _validate_object_lifecycle(self, node: ObjectNode) -> bool:
        """Return whether an object's lifecycle annotations are valid."""
        ok = True
        mustcall = _utils._mustcall_methods(node.annotations)
        object_name = node.name.text.rsplit(".", 1)[-1]
        defined = {
            definition.name.text
            for definition in node.definitions
            if definition.name.text != object_name
            and not definition.name.text.startswith("~")
        }
        for method in mustcall:
            if method not in defined:
                self._diagnose(
                    f"@mustcall method '{method}' is not defined on {node.name}",
                    node,
                )
                ok = False
        destructor_name = f"~{node.name.text.rsplit('.', 1)[-1]}"
        destructors = [
            definition
            for definition in node.definitions
            if definition.name.text.startswith("~")
        ]
        for definition in destructors:
            if definition.name.text != destructor_name:
                self._diagnose(
                    f"destructor for {node.name} must be named '{destructor_name}'",
                    definition,
                )
                ok = False
            if definition.function.params:
                self._diagnose(
                    "destructors cannot declare explicit parameters", definition
                )
                ok = False
            if definition.function.returns:
                self._diagnose(
                    "destructors cannot declare return values", definition
                )
                ok = False
            for message, violation_node in destructor_borrow_violations(
                definition.function.body
            ):
                self._diagnose(message, violation_node)
                ok = False
        return ok

