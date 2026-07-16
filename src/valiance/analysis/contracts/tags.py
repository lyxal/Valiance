"""Focused tags contract validation."""

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
from valiance.vtypes.default_types import Boolean

from ..calls import candidates as _calls
from ..calls import callable_values as _functions
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from .. import analyser as _core
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)


from ..calls.models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)










class _TagContracts:
    """Own tags contract operations."""

    def _validate_function_element_tags(
        self,
        node: FunctionNode,
        origin: ASTNode,
    ) -> None:
        """Validate function element tags during static analysis."""
        self._validate_element_tag_set(
            node.element_tags,
            origin,
            companion_tags_allowed=node.companion_tags_allowed,
        )
        annotated_types = tuple(
            param.typ
            for param in node.params or ()
            if param.typ is not None
        ) + tuple(node.returns or ()) + tuple(
            constraint
            for constraint in node.generic_constraints
            if constraint is not None
        )
        self._validate_element_tags_in_types(annotated_types, origin)

    def _validate_element_tag_set(
        self,
        tags: Iterable[T.ElementTag],
        origin: ASTNode,
        *,
        companion_tags_allowed: frozenset[T.ElementTag] | None = None,
    ) -> None:
        """Validate element tag set during static analysis."""
        tag_tuple = tuple(tags)
        positives = tuple(tag for tag in tag_tuple if not tag.absent)
        absences = tuple(tag for tag in tag_tuple if tag.absent)
        allowed = companion_tags_allowed or frozenset()
        for tag in tag_tuple:
            definition = self.env.lookup_element_tag(tag.name)
            if definition is None:
                self._diagnose(f"undeclared element tag '{tag.name}'", origin)
                continue
            if (
                not tag.absent
                and
                definition.kind is T.ElementTagKind.COMPANION
                and tag not in allowed
            ):
                self._diagnose(
                    f"companion element tag '{tag.name}' cannot be directly attached",
                    origin,
                )
        for absent in absences:
            conflict = next(
                (
                    positive
                    for positive in positives
                    if _functions._element_tag_absence_conflicts(
                        absent,
                        positive,
                        self.env.context,
                    )
                ),
                None,
            )
            if conflict is not None:
                self._diagnose(
                    f"element tag '{absent.name}' cannot be both present and absent",
                    origin,
                )
                break
        self._validate_element_tag_disjoints(positives, origin)

    def _validate_element_tags_in_types(
        self,
        types: Iterable[T.Type],
        origin: ASTNode,
    ) -> None:
        """Validate element tags in types during static analysis."""
        for typ in types:
            for tags in _functions._function_type_element_tag_sets(typ):
                self._validate_element_tag_set(
                    tags,
                    origin,
                    companion_tags_allowed=frozenset(
                        tag for tag in tags if not tag.absent
                    ),
                )

    def _validate_element_tag_disjoints(
        self,
        tags: Iterable[T.ElementTag],
        origin: ASTNode,
    ) -> None:
        """Validate element tag disjoints during static analysis."""
        seen: set[Symbol] = set()
        for tag in tags:
            disjoint = self.env.element_tag_disjoints(tag.name)
            conflict = next((name for name in seen if name in disjoint), None)
            if conflict is not None:
                self._diagnose(
                    f"element tags '{conflict}' and '{tag.name}' cannot both apply",
                    origin,
                )
                return
            seen.add(tag.name)

    def _validate_inferred_element_tags(
        self,
        node: FunctionNode,
        body_tags: frozenset[T.ElementTag],
        final_tags: frozenset[T.ElementTag],
    ) -> None:
        """Validate inferred element tags during static analysis."""
        self._validate_element_tag_disjoints(
            (tag for tag in final_tags if not tag.absent),
            node,
        )
        declared_absences = tuple(
            tag for tag in node.element_tags if tag.absent
        )
        for body_tag in body_tags:
            forbidden = next(
                (
                    absent
                    for absent in declared_absences
                    if _functions._element_tag_absence_conflicts(
                        absent,
                        body_tag,
                        self.env.context,
                    )
                ),
                None,
            )
            if forbidden is not None:
                self._diagnose(
                    f"element tag '{body_tag.name}' is required to be absent "
                    "but is used by the function body",
                    node,
                )
                return
        if not node.element_tags_explicit:
            return
        declared_properties = tuple(
            tag
            for tag in node.element_tags
            if not tag.absent
            and (definition := self.env.lookup_element_tag(tag.name)) is not None
            and definition.kind is T.ElementTagKind.PROPERTY
        )
        for tag in body_tags:
            if tag.absent or any(
                _functions._element_tag_covers(declared, tag, self.env.context)
                for declared in declared_properties
            ):
                continue
            definition = self.env.lookup_element_tag(tag.name)
            if definition is None or definition.kind is not T.ElementTagKind.PROPERTY:
                continue
            self._diagnose(
                f"element tag '{tag.name}' is used inside an explicitly "
                "constrained function but was not declared",
                node,
            )
            return

    def _validate_data_element_tag_disjoints(
        self,
        types: Iterable[T.Type],
        element_tags: Iterable[T.ElementTag],
        origin: ASTNode,
    ) -> None:
        """Validate data element tag disjoints during static analysis."""
        positive_elements = tuple(tag for tag in element_tags if not tag.absent)
        if not positive_elements:
            return
        data_tags = {
            tag.name
            for typ in types
            for tag in _functions._present_data_tags(typ)
        }
        for data_name in data_tags:
            disjoint_elements = self.env.data_tag_element_disjoints(data_name)
            for element_tag in positive_elements:
                if element_tag.name not in disjoint_elements:
                    continue
                key = (id(origin), data_name, element_tag.name)
                if key in self._reported_data_element_disjoints:
                    continue
                self._reported_data_element_disjoints.add(key)
                self._diagnose(
                    f"data tag '#{data_name}' cannot be used by an element "
                    f"with tag '{element_tag.name}'",
                    origin,
                )

    def _validate_recorded_data_element_uses(
        self,
        uses: Iterable[tuple[Symbol, Symbol]],
        origin: ASTNode,
    ) -> None:
        """Validate recorded data element uses during static analysis."""
        for data_name, element_name in uses:
            if element_name not in self.env.data_tag_element_disjoints(data_name):
                continue
            key = (id(origin), data_name, element_name)
            if key in self._reported_data_element_disjoints:
                continue
            self._reported_data_element_disjoints.add(key)
            self._diagnose(
                f"data tag '#{data_name}' cannot be used by an element "
                f"with tag '{element_name}'",
                origin,
            )

    def _validate_data_tags(
        self,
        groups: Iterable[Iterable[T.Type]],
        origin: ASTNode,
        *,
        allow_variants: bool = False,
        require_declared: bool = False,
    ) -> bool:
        """Validate data tags during static analysis and report success."""
        for group in groups:
            for typ in group:
                for tag in _core._all_data_tags(typ):
                    definition = self.env.lookup_tag(tag.name)
                    if definition is None:
                        if require_declared:
                            self._diagnose(
                                f"unknown data tag '#{tag.name}'",
                                origin,
                            )
                            return False
                        continue
                    if (
                        definition.kind is T.TagKind.VARIANT
                        and not allow_variants
                    ):
                        self._diagnose(
                            f"variant data tag '#{tag.name}' is runtime-only and "
                            "cannot appear in a compile-time signature",
                            origin,
                        )
                        return False
                for nested in _core._nested_types(typ):
                    if not isinstance(nested, T.TaggedType):
                        continue
                    rank = _calls._type_rank(T.normalize(nested.inner))
                    invalid_depths = tuple(
                        tag for tag in nested.tags if tag.depth > rank
                    )
                    if invalid_depths:
                        tag = sorted(invalid_depths)[0]
                        self._diagnose(
                            f"data tag '#{tag.name}{'+' * tag.depth}' has depth "
                            f"{tag.depth}, but {T.show(nested.inner)} has rank {rank}",
                            origin,
                        )
                        return False
                    positive = {
                        (tag.name, tag.depth)
                        for tag in nested.tags
                        if not tag.absent
                    }
                    negative = {
                        (tag.name, tag.depth)
                        for tag in nested.tags
                        if tag.absent
                    }
                    conflict = positive.intersection(negative)
                    if conflict:
                        name, depth = sorted(conflict)[0]
                        suffix = "+" * depth
                        self._diagnose(
                            f"data tag '#{name}{suffix}' cannot be both present "
                            "and absent",
                            origin,
                        )
                        return False
                conflict = _calls._disjoint_data_tags(typ, self.env.context)
                if conflict is None:
                    continue
                left, right = conflict
                self._diagnose(
                    f"data tags '#{left.text}' and '#{right.text}' cannot both apply",
                    origin,
                )
                return False
        return True

