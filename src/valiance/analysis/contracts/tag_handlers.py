"""Tag declaration, overlay, and application handlers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.analysis.lints import KNOWN_LINT_CODES, finding
import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    AssertNode,
    AtNode,
    CastNode,
    DictLiteralNode,
    ElementTagDeclarationNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionParam,
    ImportNode,
    IndexAccessNode,
    IndexSetNode,
    ListLiteralNode,
    LintSuppressionNode,
    NumberLiteralNode,
    ObjectNode,
    PopNNode,
    RecordLiteralNode,
    ReturnNode,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TagDeclarationNode,
    TagOverlayNode,
    TupleLiteralNode,
    TypedAssertNode,
    TypedAtNode,
    TypedCallNode,
    TypedForNode,
    TypedFunctionNode,
    TypedIfNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedUnfoldNode,
    TypedWhileNode,
    UnfoldNode,
)
from valiance.asts.nodes import (
    BreakNode,
    CallNode,
    FieldAccessNode,
    FieldSetNode,
    ForNode,
    GetVariableNode,
    IfNode,
    SetVariableNode,
    SetVariablesNode,
    WhileNode,
)
from valiance.modules_system.modules import (
    ModuleLoadError,
    import_environment_facts,
    import_objects,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.default_types import Boolean
from valiance.vtypes.relations import merge_stacks

from .. import analyser as _core
from ..calls import callable_values as _functions
from ..calls import candidates as _calls
from ..control_flow import patterns as _patterns
from .. import _analyser_utils as _utils



def _runtime_tag_removal_closure(
    tags: Iterable[T.DataTag],
    ctx: T.Context,
) -> tuple[T.DataTag, ...]:
    """Remove variant evidence whenever its computed parent is removed."""
    pending = list(tags)
    removed = set(pending)
    while pending:
        current = pending.pop()
        for variant, parent in ctx.tag_parents.items():
            candidate = T.DataTag(variant.text, current.depth)
            if parent.text == current.name and candidate not in removed:
                removed.add(candidate)
                pending.append(candidate)
    return tuple(sorted(removed))

@_core.register(TagDeclarationNode)
def _tag_declaration_node(
    self: _core.Analyser,
    node: TagDeclarationNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `TagDeclarationNode` node and return the surviving branches."""
    if node.disjoint is not None:
        if isinstance(node.disjoint, T.DataTag):
            self.env.add_disjoint_tags(node.tag.name, node.disjoint.name)
        else:
            self.env.add_disjoint_data_element_tags(node.tag.name, node.disjoint)
    elif node.parent is not None:
        parent = self.env.lookup_tag(node.parent.name)
        if parent is None:
            self._diagnose(
                f"variant tag '#{node.tag.name}' requires declared computed "
                f"parent '#{node.parent.name}'",
                node,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        if parent.kind is not T.TagKind.COMPUTED:
            self._diagnose(
                f"variant tag '#{node.tag.name}' parent '#{node.parent.name}' "
                "must be computed",
                node,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        self.env.add_variant_tag(node.tag.name, node.parent.name)
    elif node.kind == Symbol("constructed"):
        self.env.add_constructed_tag(node.tag.name)
    elif node.kind == Symbol("unit"):
        self.env.add_unit_tag(node.tag.name)
    else:
        self.env.add_computed_tag(node.tag.name)

    return _core.BranchSet((branch.emit(TypedNode(node, None)),))

@_core.register(ElementTagDeclarationNode)
def _element_tag_declaration_node(
    self: _core.Analyser,
    node: ElementTagDeclarationNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ElementTagDeclarationNode` node and return the surviving branches."""
    if node.disjoint is not None:
        if isinstance(node.disjoint, T.DataTag):
            self.env.add_disjoint_data_element_tags(node.disjoint.name, node.name)
        else:
            self.env.add_disjoint_element_tags(node.name, node.disjoint)
    elif node.kind == Symbol("companion"):
        self.env.add_companion_element_tag(node.name)
    else:
        self.env.add_property_element_tag(node.name)

    return _core.BranchSet((branch.emit(TypedNode(node, None)),))

@_core.register(TagOverlayNode)
def _tag_overlay_node(
    self: _core.Analyser,
    node: TagOverlayNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `TagOverlayNode` node and return the surviving branches."""
    if self.env.lookup_tag(node.tag.name) is None:
        self._diagnose(f"unknown data tag '#{node.tag.name}'", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    public = node.visibility == Symbol("public")
    for element in node.elements:
        for params, returns in node.signatures:
            if not self._validate_data_tags((params, returns), node):
                continue
            overlay_error = _tag_overlay_contract_error(
                node.tag.name,
                params,
                returns,
                self.env.context,
            )
            if overlay_error is not None:
                self._diagnose(overlay_error, node)
                continue
            overload = T.Overload(params=params, returns=returns)
            if node.generics:
                overload = _functions._genericize_overload(overload, node.generics)
            self.env.define_tag_overlay(
                node.tag.name,
                element,
                overload,
                public=public,
            )

    return _core.BranchSet((branch.emit(TypedNode(node, None)),))

def _tag_overlay_contract_error(
    name: str,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    ctx: T.Context,
) -> str | None:
    """Return why an overlay would violate tag ownership or rank flow."""
    source_tags = tuple(
        (tag, _calls._type_rank(T.normalize(param)))
        for param in params
        for tag in _top_level_data_tags(param)
        if tag.name == name and not tag.absent
    )
    if not source_tags:
        return f"tag overlay '#{name}' must require that tag on an input"

    for ret in returns:
        return_rank = _calls._type_rank(T.normalize(ret))
        for tag in _top_level_data_tags(ret):
            if tag.name != name:
                return (
                    f"tag overlay '#{name}' cannot add, remove, or preserve "
                    f"foreign tag '#{tag.name}' in its return contract"
                )
            if tag.absent or not ctx.is_constructed_like_tag(name):
                continue
            valid_source = any(
                return_rank >= max(source_rank - source.depth, 0)
                and tag.depth == max(return_rank - 1, 0)
                for source, source_rank in source_tags
            )
            if not valid_source:
                return (
                    f"constructed tag overlay '#{name}' has unsafe rank/depth "
                    "flow in its return contract"
                )
    return None

def _top_level_data_tags(typ: T.Type) -> tuple[T.DataTag, ...]:
    """Return tags decorating the value represented by ``typ``."""
    normalized = T.normalize(typ)
    return tuple(normalized.tags) if isinstance(normalized, T.TaggedType) else ()

@_core.register(TagApplicationNode)
def _tag_application_node(
    self: _core.Analyser,
    node: TagApplicationNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `TagApplicationNode` node and return the surviving branches."""
    definition = self.env.lookup_tag(node.tag.name)
    if definition is None:
        self._diagnose(f"unknown data tag '#{node.tag.name}'", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    sourced = branch.source_arguments((T.V("_tagged_value"),))
    if sourced is None:
        self._diagnose(
            f"empty stack when applying tag '{_calls._show_tag(node.tag)}'",
            node,
        )
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    (value_type,), base_branch = sourced
    value_rank = _calls._type_rank(T.normalize(value_type))
    if node.tag.depth > value_rank:
        self._diagnose(
            f"data tag '{_calls._show_tag(node.tag)}' has depth "
            f"{node.tag.depth}, but {T.show(value_type)} has rank {value_rank}",
            node,
        )
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    validator: T.AppliedOverload | None = None
    validator_index: int | None = None
    validator_runtime_name: Symbol | None = None
    validator_plans: list[tuple[Symbol, int]] = []
    added_tags: tuple[T.DataTag, ...] = ()
    removed_tags: tuple[T.DataTag, ...] = ()
    if node.tag.absent:
        tagged = _calls._remove_data_tag(value_type, node.tag)
        if tagged is None:
            self._diagnose(
                f"cannot remove absent tag '{_calls._show_tag(node.tag)}' from "
                f"{value_type}",
                node,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        removed_tags = _runtime_tag_removal_closure(
            (T.DataTag(node.tag.name, node.tag.depth),),
            self.env.context,
        )
    else:
        added = [T.DataTag(node.tag.name, node.tag.depth)]
        parent = self.env.context.tag_parent(node.tag.name)
        if parent is not None:
            added.append(T.DataTag(parent.text, node.tag.depth))
            tagged = _calls._with_data_tags(
                value_type,
                (T.DataTag(parent.text, node.tag.depth),),
                self.env.context,
            )
        else:
            tagged = _calls._with_data_tags(value_type, (node.tag,), self.env.context)
        added_tags = tuple(added)
        disjoint_names: set[Symbol] = set()
        for added_tag in added_tags:
            disjoint_names.update(self.env.context.tag_disjoints(added_tag.name))
        removed_tags = _runtime_tag_removal_closure(
            (
                T.DataTag(str(name), node.tag.depth)
                for name in sorted(disjoint_names, key=str)
            ),
            self.env.context,
        )
        for removed_tag in removed_tags:
            without = _calls._remove_data_tag(tagged, removed_tag)
            if without is not None:
                tagged = without
        for applied_tag in added_tags:
            validator_name = Symbol(f"#{applied_tag.name}")
            validator_overloads = self.env.overloads_for(validator_name)
            if not validator_overloads:
                continue
            resolved = T.resolve_overload_result(
                validator_overloads,
                (value_type,),
                self.env.context,
            )
            matching = tuple(
                (index, overload)
                for index, overload in enumerate(validator_overloads)
                if T.try_apply_overload(
                    overload,
                    (value_type,),
                    self.env.context,
                ).applied
                is not None
            )
            if resolved is None:
                if matching:
                    self._diagnose(
                        f"ambiguous validator overloads for '{validator_name}' "
                        f"with {T.show(value_type)}",
                        node,
                    )
                else:
                    self._diagnose(
                        f"no validator overload for '{validator_name}' matches "
                        f"{T.show(value_type)}",
                        node,
                    )
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            selected_index = next(
                index
                for index, overload in enumerate(validator_overloads)
                if overload is resolved.overload or overload == resolved.overload
            )
            selected = validator_overloads[selected_index]
            if not _calls._validator_overload_ok(selected, self.env.context):
                self._diagnose(
                    f"tag validator '{validator_name}' must return " "#boolean Number",
                    node,
                )
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            applied = T.try_apply_overload(
                selected,
                (value_type,),
                self.env.context,
            ).applied
            assert applied is not None
            static_result = self.env.tag_validator_static_result(
                validator_name,
                selected_index,
            )
            if static_result is True:
                continue
            elif static_result is False:
                self._diagnose(
                    f"tag validator '{validator_name}' is statically false",
                    node,
                )
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            runtime_name = self.env.runtime_name_for(validator_name) or validator_name
            validator_plans.append((runtime_name, selected_index))
            if applied_tag.name == node.tag.name:
                validator = applied
                validator_index = selected_index
                validator_runtime_name = runtime_name

    stack = base_branch.stack.push(tagged)
    typed = TypedTagApplicationNode(
        node,
        tagged,
        validator,
        validator_index,
        added_tags,
        removed_tags,
        validator_runtime_name,
        tuple(validator_plans),
    )
    return _core.BranchSet((base_branch.with_stack(stack).emit(typed),))

