"""Pure operations supporting immutable analysis state transformations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import fields, is_dataclass, replace
from typing import cast

import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    FunctionOverloadTyping,
    ListLiteralNode,
    StringLiteralNode,
    TypedAtNode,
    TypedCallNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedFunctionNode,
    TypedIfNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedLiteralNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedUnfoldNode,
)
from valiance.vtypes.symbols import Symbol


def is_never(typ: T.Type) -> bool:
    """Return whether a type is the non-continuing ``Never`` type."""
    return isinstance(T.normalize(typ), T.NeverType)


def transform_overload_types(
    overload: T.Overload,
    transform: Callable[[T.Type], T.Type],
    *,
    element_tags: frozenset[T.ElementTag] | None = None,
) -> T.Overload:
    """Apply a transformation to every parameter and return type."""
    return replace(
        overload,
        params=tuple(transform(param) for param in overload.params),
        returns=tuple(transform(ret) for ret in overload.returns),
        element_tags=overload.element_tags if element_tags is None else element_tags,
    )


def transform_type_children(
    typ: T.Type,
    transform: Callable[[T.Type], T.Type],
    *,
    element_tags: Callable[[frozenset[T.ElementTag]], frozenset[T.ElementTag]] = lambda tags: tags,
) -> T.Type:
    """Rebuild a type after transforming each direct child."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return T.N(typ.name, *(transform(arg) for arg in typ.args))
    if isinstance(typ, T.UnionType):
        return T.U(*(transform(item) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(transform(item) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(transform(item) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(*(T.TupleTypeItem(transform(item.typ), item.repeated) for item in typ.items))
    if isinstance(typ, T.RowType):
        return T.Row(transform(typ.base), *(T.Field(item.name, transform(item.typ)) for item in typ.fields))
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), transform(typ.base), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(None, None, element_tags(typ.element_tags))
        return T.Fn(tuple(transform(item) for item in typ.params), tuple(transform(item) for item in typ.returns), element_tags(typ.element_tags))
    if isinstance(typ, T.TaggedType):
        return T.Tagged(transform(typ.inner), *typ.tags, exact=typ.exact)
    if isinstance(typ, T.NoVecType):
        return T.NoVec(transform(typ.inner))
    if isinstance(typ, T.ExactType):
        return T.Exact(transform(typ.inner))
    return typ


def present_data_tags(typ: T.Type) -> Iterator[T.DataTag]:
    """Yield present data tags nested anywhere in a type."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        yield from (tag for tag in typ.tags if not tag.absent)
        yield from present_data_tags(typ.inner)
    elif isinstance(typ, T.NominalType):
        for arg in typ.args: yield from present_data_tags(arg)
    elif isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items: yield from present_data_tags(item)
    elif isinstance(typ, T.TupleType):
        for item in typ.params: yield from present_data_tags(item)
    elif isinstance(typ, T.VariadicTupleType):
        for item in typ.items: yield from present_data_tags(item.typ)
    elif isinstance(typ, T.RowType):
        yield from present_data_tags(typ.base)
        for item in typ.fields: yield from present_data_tags(item.typ)
    elif isinstance(typ, T.CollectionType):
        yield from present_data_tags(typ.base)
    elif isinstance(typ, T.FunctionType):
        for tag in typ.element_tags:
            for arg in tag.args: yield from present_data_tags(arg)
        for item in (*(typ.params or ()), *(typ.returns or ())): yield from present_data_tags(item)
    elif isinstance(typ, (T.NoVecType, T.ExactType)):
        yield from present_data_tags(typ.inner)
    elif isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for item in (*requirement.overload.params, *requirement.overload.returns): yield from present_data_tags(item)
    elif isinstance(typ, T.OverloadSetType):
        for overload in typ.overloads:
            for item in (*overload.params, *overload.returns): yield from present_data_tags(item)
def _refine_stack(stack: T.TypeStack, old: T.Type, new: T.Type) -> T.TypeStack:
    """Refine stack during static analysis."""
    return T.TypeStack(tuple(_refine_type(item, old, new) for item in stack.items))


def _refine_items(
    items: tuple[tuple[Symbol, T.Type], ...],
    old: T.Type,
    new: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Refine items during static analysis."""
    return tuple((name, _refine_type(typ, old, new)) for name, typ in items)


def _refine_typed_body(
    typed_body: tuple[TypedNode, ...],
    old: T.Type,
    new: T.Type,
) -> tuple[TypedNode, ...]:
    """Refine typed body during static analysis."""
    return tuple(_refine_typed_node(node, old, new) for node in typed_body)


def _refine_typed_node(typed_node: TypedNode, old: T.Type, new: T.Type) -> TypedNode:
    """Refine typed node during static analysis."""
    typ = None if typed_node.typ is None else _refine_type(typed_node.typ, old, new)
    if isinstance(typed_node, TypedImportedFunctionNode):
        return TypedImportedFunctionNode(
            typed_node.node,
            typ,
            tuple(
                FunctionOverloadTyping(
                    _refine_type(overload.typ, old, new),
                    _refine_typed_body(overload.body, old, new),
                    overload.overload,
                )
                for overload in typed_node.overloads
            ),
            typed_node.dispatch_plan,
            typed_node.runtime_name,
        )
    if isinstance(typed_node, TypedFunctionNode):
        return TypedFunctionNode(
            typed_node.node,
            typ,
            tuple(
                FunctionOverloadTyping(
                    _refine_type(overload.typ, old, new),
                    _refine_typed_body(overload.body, old, new),
                    overload.overload,
                )
                for overload in typed_node.overloads
            ),
            typed_node.dispatch_plan,
        )
    if isinstance(typed_node, TypedLiteralNode):
        return TypedLiteralNode(
            typed_node.node,
            typ,
            tuple(_refine_typed_body(item, old, new) for item in typed_node.items),
        )
    if isinstance(typed_node, TypedTagApplicationNode):
        return TypedTagApplicationNode(
            typed_node.node,
            typ,
            typed_node.validator,
            typed_node.validator_index,
            typed_node.added_tags,
            typed_node.removed_tags,
            typed_node.validator_runtime_name,
            typed_node.validator_plans,
        )
    if isinstance(typed_node, TypedElementNode):
        return TypedElementNode(
            typed_node.node,
            typ,
            typed_node.overload,
            typed_node.overload_index,
            typed_node.modifier_args,
            typed_node.call_arg_order,
            typed_node.call_overload_index,
            _refine_typed_extension(typed_node.extension, old, new),
            typed_node.runtime_name,
        )
    if isinstance(typed_node, TypedCallNode):
        return TypedCallNode(
            typed_node.node,
            typ,
            typed_node.overload,
        )
    if isinstance(typed_node, TypedIfNode):
        return TypedIfNode(
            typed_node.node,
            typ,
            _refine_typed_body(typed_node.condition, old, new),
            _refine_typed_body(typed_node.then_branch, old, new),
            _refine_typed_body(typed_node.else_branch, old, new),
            typed_node.then_padding,
            typed_node.else_padding,
        )
    if isinstance(typed_node, TypedUnfoldNode):
        function = typed_node.function
        refined_function = (
            None
            if function is None
            else cast(
                TypedFunctionNode,
                _refine_typed_node(function, old, new),
            )
        )
        return TypedUnfoldNode(
            typed_node.node,
            typ,
            typed_node.state_arity,
            refined_function,
        )
    if isinstance(typed_node, TypedAtNode):
        function = typed_node.function
        refined_function = (
            None
            if function is None
            else cast(
                TypedFunctionNode,
                _refine_typed_node(function, old, new),
            )
        )
        return TypedAtNode(
            typed_node.node,
            typ,
            refined_function,
            typed_node.overload,
            typed_node.function_overload_index,
        )
    if isinstance(typed_node, TypedImportedObjectNode):
        return TypedImportedObjectNode(
            typed_node.node,
            typ,
            typed_node.runtime_name,
        )
    return TypedNode(typed_node.node, typ)


def _refine_typed_extension(
    extension: TypedElementExtension | None,
    old: T.Type,
    new: T.Type,
) -> TypedElementExtension | None:
    """Refine typed extension during static analysis."""
    if extension is None:
        return None

    def refine_function(function: TypedFunctionNode | None) -> TypedFunctionNode | None:
        """Refine function during static analysis."""
        if function is None:
            return None
        refined = _refine_typed_node(function, old, new)
        assert isinstance(refined, TypedFunctionNode)
        return refined

    return TypedElementExtension(
        default=refine_function(extension.default),
        rules=tuple(
            TypedExtensionPatternRule(
                rule.pattern,
                cast(TypedFunctionNode, _refine_typed_node(rule.function, old, new)),
            )
            for rule in extension.rules
        ),
        selector=refine_function(extension.selector),
    )


def _refine_type(typ: T.Type, old: T.Type, new: T.Type) -> T.Type:
    """Refine type during static analysis."""
    typ = T.normalize(typ)
    new = _erase_absent_tag_requirements(new)
    if T.same(typ, old):
        return new
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    transform_overload_types(
                        requirement.overload,
                        lambda item: _refine_type(item, old, new),
                    ),
                )
                for requirement in typ.requirements
            ),
        )
    if isinstance(typ, T.ExactType):
        return T.Exact(_refine_type(typ.inner, old, new))
    return transform_type_children(
        typ,
        lambda child: _refine_type(child, old, new),
    )


def _refine_input_requirement(typ: T.Type, old: T.Type, new: T.Type) -> T.Type:
    """Refine a function-input fact while preserving negative tag constraints."""
    typ = T.normalize(typ)
    if T.same(typ, old):
        return new
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    transform_overload_types(
                        requirement.overload,
                        lambda item: _refine_input_requirement(item, old, new),
                    ),
                )
                for requirement in typ.requirements
            ),
        )
    if isinstance(typ, T.ExactType):
        return T.Exact(_refine_input_requirement(typ.inner, old, new))
    return transform_type_children(
        typ,
        lambda child: _refine_input_requirement(child, old, new),
    )


def _refine_input_requirement_items(
    items: tuple[tuple[Symbol, T.Type], ...], old: T.Type, new: T.Type
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Refine named input facts while preserving negative tag constraints."""
    return tuple(
        (name, _refine_input_requirement(typ, old, new)) for name, typ in items
    )


def _erase_absent_tag_requirements(typ: T.Type) -> T.Type:
    """Compute erase absent tag requirements during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType) and all(tag.absent for tag in typ.tags):
        return typ.inner
    return typ


def _lookup(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
) -> T.Type | None:
    """Compute lookup during static analysis."""
    for key, typ in items:
        if key == name:
            return typ
    return None


def _assignment_error(
    name: Symbol,
    source: T.Type,
    target: T.Type,
    ctx: T.Context,
) -> str | None:
    """Return the error description for assignment during static analysis."""
    if _assignment_stored_type(target, source, ctx) is not None:
        return None
    return (
        f"cannot assign {T.show(source)} to variable '{name}' of type {T.show(target)}"
    )


def _assignment_stored_type(
    existing: T.Type,
    source: T.Type,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of assignment stored during static analysis."""
    if T.assignable(source, existing, ctx):
        return existing
    if T.assignable(existing, source, ctx):
        return source
    return None


def _mustcall_methods(annotations: tuple[ASTNode, ...]) -> tuple[str, ...]:
    """Compute mustcall methods during static analysis."""
    for annotation in annotations:
        if not isinstance(annotation, AnnotationNode):
            continue
        if annotation.name.text != "mustcall":
            continue
        kwargs = dict(annotation.kwargs)
        for key in (Symbol("all"), Symbol("any")):
            value = kwargs.get(key)
            if not isinstance(value, ListLiteralNode):
                continue
            methods: list[str] = []
            for item in value.items:
                if len(item) != 1 or not isinstance(item[0], StringLiteralNode):
                    return ()
                methods.append(item[0].value)
            return tuple(methods)
    return ()


def _child_symbol(parent: Symbol, child: Symbol) -> Symbol:
    """Compute child symbol during static analysis."""
    return Symbol(child.text, (*parent.namespace, parent.text, *child.namespace))


def _set_item(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
    typ: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Compute set item during static analysis."""
    result = {key: value for key, value in items}
    result[name] = typ
    return _sorted_items(result.items())


def _set_symbol_flag(
    items: tuple[Symbol, ...],
    name: Symbol,
    enabled: bool,
) -> tuple[Symbol, ...]:
    """Compute set symbol flag during static analysis."""
    result = set(items)
    if enabled:
        result.add(name)
    else:
        result.discard(name)
    return tuple(sorted(result))


def _sorted_items(
    items: Iterable[tuple[Symbol, T.Type]],
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Collect the items for sorted during static analysis."""
    return tuple(sorted(items, key=lambda item: item[0]))

# Local aliases keep the transformation code concise and preserve its proven behaviour.
_is_never = is_never
