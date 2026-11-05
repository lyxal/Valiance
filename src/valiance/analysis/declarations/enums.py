"""Focused enums declaration analysis."""

from __future__ import annotations


import valiance.vtypes as T
from valiance.asts import NumberLiteralNode, StringLiteralNode, ObjectNode, TypedNode

from ..support import analysis_utils as _utils
from ..state import AnalysisBranch, BranchSet
class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

class _EnumDeclarations:
    """Own enums declaration operations."""

    def _enum_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build the definition for enum during static analysis."""
        value_type = T.V(node.generics[0].text) if node.generics else None
        # Enum values are literal-only at lowering time. Check their declared
        # type before publishing the enum's niladic member elements, so a
        # mismatched payload cannot acquire an incorrect static .value type.
        declared_value_type = T.N(node.generics[0]) if node.generics else None
        for member in node.enum_members:
            if declared_value_type is None:
                if member.value:
                    self._diagnose(
                        f"unvalued enum member '{member.name}' cannot store a value",
                        member,
                    )
                continue
            if not member.value:
                self._diagnose(
                    f"valued enum member '{member.name}' requires a value",
                    member,
                )
                continue
            if len(member.value) != 1:
                self._diagnose("enum member value must be a single literal", member)
                continue
            literal = member.value[0]
            if isinstance(literal, NumberLiteralNode):
                actual = _utils._number_literal_type(literal.value)
            elif isinstance(literal, StringLiteralNode):
                actual = T.String
            else:
                self._diagnose("enum member value must be a literal", member)
                continue
            if not T.compatible(actual, declared_value_type, self.env.context):
                self._diagnose(
                    f"enum member '{member.name}' value type {T.show(actual)} "
                    f"is incompatible with {T.show(declared_value_type)}",
                    member,
                )
        members = tuple(
            T.EnumMemberDefinition(
                _utils._child_symbol(node.name, member.name),
                value_type,
                bool(member.value),
            )
            for member in node.enum_members
        )
        self.env.define_enum(node.name, members, value_type=value_type)
        return BranchSet(
            (
                branch.emit(
                    TypedNode(node, _utils._declared_nominal(node.name, node.generics))
                ),
            )
        )

