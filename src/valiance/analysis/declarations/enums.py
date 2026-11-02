"""Focused enums declaration analysis."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import (
    dataclass,
    field,
    fields,
    is_dataclass,
    replace,
)
from enum import Enum, auto
from hashlib import sha1
from itertools import count
from pathlib import Path
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
import valiance.vtypes as T
import valiance.analysis.contracts.where_clauses as static_where
from valiance.elements.builtins import default_environment
from valiance.analysis.lints import (
    DEFAULT_REGISTRY as DEFAULT_LINT_REGISTRY,
    BlockLintContext,
    LintFinding,
    LintRegistry,
    MatchLintContext,
    NodeLintContext,
)
from valiance.asts import (
    AnnotationNode,
    ASTNode,
    BindingPatternNode,
    DefineNode,
    ElementExtension,
    ElementNode,
    EnumMemberNode,
    NumberLiteralNode,
    StringLiteralNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    ImportComponent,
    ImportPath,
    ImportSpec,
    ListPatternNode,
    MatchCaseNode,
    MatchNode,
    MatchPatternNode,
    ObjectNode,
    OrPatternNode,
    PopNNode,
    SourceLocation,
    TraitRequirementNode,
    TryHandlerNode,
    TryNode,
    TypePatternNode,
    TypedCallNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedFunctionNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedMatchNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedTryNode,
    VariantMemberNode,
    is_catch_all_match_case,
)
from valiance.asts.nodes import GetVariableNode, ObjectFieldNode
from valiance.modules_system.modules import ModuleLoader, ModuleLoadError, import_definitions
from valiance.asts.object_constructors import (
    constructor_definitions,
    definitely_initialized_fields,
    prepare_constructor_body,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.default_types import Boolean

from ..calls import candidates as _calls
from ..calls import callable_values as _functions
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
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

