"""Field receiver sourcing, access typing, and visibility rules."""

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
    is_default_match_case,
)
from valiance.asts.nodes import GetVariableNode, ObjectFieldNode
from valiance.modules_system.modules import ModuleLoader, ModuleLoadError, import_definitions
from valiance.asts.object_constructors import (
    constructor_definitions,
    definitely_initialized_fields,
    prepare_constructor_body,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.structural import nested_types
from valiance.vtypes.default_types import Boolean

from ..calls import candidates as _calls
from ..calls import callable_values as _functions
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)



from ..calls.models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)














class _FieldExpressions:
    """Own field-access expression semantics."""

    def _source_field_receiver(
        self,
        branch: AnalysisBranch,
        name: Symbol,
        *,
        optional_safe: bool = False,
    ) -> tuple[T.Type, T.Type | None, AnalysisBranch] | None:
        """Source field receiver during static analysis."""
        if branch.stack:
            receiver_type = branch.stack[-1]
            popped = branch.with_stack(branch.stack.pop())
            resolver = self._safe_field_type if optional_safe else self._field_type
            field_type, refined_receiver = resolver(receiver_type, name, popped)
            if refined_receiver is not None:
                popped = popped.refine_type(receiver_type, refined_receiver)
                receiver_type = refined_receiver
            return receiver_type, field_type, popped

        if (
            branch.input_mode is InputMode.CYCLE_EXPLICIT_PARAMS
            and branch.cycle_params
        ):
            sourced = branch.source_arguments((branch.cycle_params[0],))
            if sourced is None:
                return None
            (receiver_type,), popped = sourced
            resolver = self._safe_field_type if optional_safe else self._field_type
            field_type, refined_receiver = resolver(
                receiver_type,
                name,
                popped,
            )
            if refined_receiver is not None:
                popped = popped.refine_type(receiver_type, refined_receiver)
                receiver_type = refined_receiver
            return receiver_type, field_type, popped

        if branch.input_mode is not InputMode.INFER_INPUTS:
            return None

        base = _functions._anonymous_type_var(branch, 1)
        field_type = _functions._anonymous_type_var(branch, 2)
        present_type = T.Row(base, T.Field(name, field_type))
        receiver_type = T.optional(present_type) if optional_safe else present_type
        result_type = (
            field_type
            if _patterns._optional_payload_type(field_type, strict=True) is not None
            else T.optional(field_type)
            if optional_safe
            else field_type
        )
        return (
            receiver_type,
            result_type,
            replace(branch, inputs=branch.inputs + (receiver_type,)),
        )

    def _safe_field_type(
        self,
        receiver_type: T.Type,
        name: Symbol,
        branch: AnalysisBranch,
        *,
        write: bool = False,
    ) -> tuple[T.Type | None, T.Type | None]:
        """Determine a field type through an optional present value."""
        receiver_type = T.normalize(receiver_type)
        if not write and isinstance(receiver_type, T.CollectionType):
            field_type, refined_base = self._safe_field_type(
                receiver_type.base,
                name,
                branch,
            )
            if field_type is None:
                return None, None
            refined = (
                receiver_type
                if refined_base is None
                else T.C(type(receiver_type), refined_base, receiver_type.rank)
            )
            return T.C(type(receiver_type), field_type, receiver_type.rank), refined

        payload_type = _patterns._optional_payload_type(receiver_type, strict=True)
        if payload_type is None:
            return None, None
        field_type, refined_payload = self._field_type(
            payload_type,
            name,
            branch,
            write=write,
        )
        if field_type is None:
            return None, None
        refined_receiver = (
            None if refined_payload is None else T.optional(refined_payload)
        )
        if write:
            return field_type, refined_receiver
        result_type = (
            field_type
            if _patterns._optional_payload_type(field_type, strict=True) is not None
            else T.optional(field_type)
        )
        return result_type, refined_receiver

    def _field_type(
        self,
        receiver_type: T.Type,
        name: Symbol,
        branch: AnalysisBranch,
        *,
        write: bool = False,
    ) -> tuple[T.Type | None, T.Type | None]:
        """Determine the type of field during static analysis."""
        receiver_type = T.normalize(receiver_type)
        if isinstance(receiver_type, T.RowType):
            existing = _utils._row_field_type(receiver_type, name)
            if write:
                return (existing, None) if existing is not None else (None, None)
            if existing is not None:
                return existing, None
            field_type = _functions._anonymous_type_var(branch, 1)
            return (
                field_type,
                T.Row(
                    receiver_type.base,
                    *receiver_type.fields,
                    T.Field(name, field_type),
                ),
            )

        if isinstance(receiver_type, T.VarType):
            if write:
                return None, None
            field_type = _functions._anonymous_type_var(branch, 1)
            return field_type, T.Row(receiver_type, T.Field(name, field_type))

        if isinstance(receiver_type, T.NominalType):
            definition = self.env.lookup_object(receiver_type.name)
            attribute = None if definition is None else definition.attribute(name)
            if attribute is None:
                return None, None
            if not self._can_access_attribute(
                receiver_type.name,
                attribute,
                write=write,
            ):
                return None, None
            field_variables = {
                variable.name: variable
                for variable in nested_types(attribute.typ)
                if isinstance(variable, T.VarType)
            }
            substitution = {
                (
                    field_variables[generic.text].identity
                    if generic.text in field_variables
                    and field_variables[generic.text].identity is not None
                    else generic.text
                ): arg
                for generic, arg in zip(
                    definition.generics,
                    receiver_type.args,
                    strict=False,
                )
            }
            return _calls._substitute_branch_type(attribute.typ, substitution), None

        if isinstance(receiver_type, T.CollectionType):
            field_type, refined_base = self._field_type(
                receiver_type.base,
                name,
                branch,
                write=write,
            )
            if field_type is None:
                return None, None
            refined = (
                receiver_type
                if refined_base is None
                else T.C(type(receiver_type), refined_base, receiver_type.rank)
            )
            return T.C(type(receiver_type), field_type, receiver_type.rank), refined

        return None, None

    def _can_access_attribute(
        self,
        receiver_name: Symbol,
        attribute: T.ObjectAttribute,
        *,
        write: bool,
    ) -> bool:
        """Return whether the analyser can access attribute."""
        access = attribute.access.text
        if access == "public":
            return True
        if access == "readable" and not write:
            return True
        return receiver_name in self._friendly_owners

