"""Extensible compile-time annotation hooks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import permutations

import valiance.types as T
from valiance.asts import (
    AnnotationNode,
    ASTNode,
    DefineNode,
    ElementNode,
    FieldAccessNode,
    FunctionNode,
    FunctionOverloadTyping,
    GetVariableNode,
    ListLiteralNode,
    ObjectFieldNode,
    ObjectNode,
    StringLiteralNode,
    TypedElementNode,
    TypedNode,
)
from valiance.symbols import Symbol
from valiance.types.default_types import String

AnnotationValidator = Callable[[AnnotationNode, str, ASTNode], tuple[str, ...]]
FunctionTransform = Callable[[FunctionNode, tuple[AnnotationNode, ...]], FunctionNode]
ObjectTransform = Callable[[ObjectNode, tuple[AnnotationNode, ...]], ObjectNode]
OverloadTransform = Callable[[T.Overload, tuple[AnnotationNode, ...]], T.Overload]


@dataclass(frozen=True)
class AnnotationSpec:
    """One compiler annotation extension point."""

    name: str
    targets: frozenset[str]
    validate: AnnotationValidator | None = None
    transform_function: FunctionTransform | None = None
    transform_object: ObjectTransform | None = None
    transform_overload: OverloadTransform | None = None


class AnnotationRegistry:
    """Registry for built-in and future plugin-provided annotations."""

    def __init__(self) -> None:
        self._specs: dict[str, AnnotationSpec] = {}

    def register(self, spec: AnnotationSpec) -> None:
        self._specs[spec.name] = spec

    def spec(self, name: str) -> AnnotationSpec | None:
        return self._specs.get(name)

    def validate(
        self,
        annotations: tuple[ASTNode, ...],
        target: str,
        node: ASTNode,
    ) -> tuple[str, ...]:
        diagnostics: list[str] = []
        for annotation in annotation_nodes(annotations):
            spec = self.spec(annotation.name.text)
            if spec is None or target not in spec.targets:
                diagnostics.append(
                    f"annotation '@{annotation.name}' cannot be used on {target}"
                )
                continue
            if spec.validate is not None:
                diagnostics.extend(spec.validate(annotation, target, node))
        return tuple(diagnostics)

    def transform_function(
        self,
        function: FunctionNode,
        annotations: tuple[ASTNode, ...],
    ) -> FunctionNode:
        current = replace(
            function,
            annotations=function.annotations + annotations,
        )
        nodes = annotation_nodes(annotations)
        for annotation in nodes:
            spec = self.spec(annotation.name.text)
            if spec is not None and spec.transform_function is not None:
                current = spec.transform_function(current, nodes)
        return current

    def transform_object(self, node: ObjectNode) -> ObjectNode:
        current = node
        nodes = annotation_nodes(node.annotations)
        for annotation in nodes:
            spec = self.spec(annotation.name.text)
            if spec is not None and spec.transform_object is not None:
                current = spec.transform_object(current, nodes)
        return current

    def transform_overload(
        self,
        overload: T.Overload,
        annotations: tuple[ASTNode, ...],
    ) -> T.Overload:
        current = overload
        nodes = annotation_nodes(annotations)
        for annotation in nodes:
            spec = self.spec(annotation.name.text)
            if spec is not None and spec.transform_overload is not None:
                current = spec.transform_overload(current, nodes)
        return current


DEFAULT_REGISTRY = AnnotationRegistry()


def register_annotation(spec: AnnotationSpec) -> None:
    """Register an annotation for this process."""
    DEFAULT_REGISTRY.register(spec)


def annotation_nodes(annotations: tuple[ASTNode, ...]) -> tuple[AnnotationNode, ...]:
    return tuple(
        annotation
        for annotation in annotations
        if isinstance(annotation, AnnotationNode)
    )


def has_annotation(annotations: tuple[ASTNode, ...], name: str) -> bool:
    return any(
        annotation.name.text == name
        for annotation in annotation_nodes(annotations)
    )


def annotation_error_message(annotations: tuple[ASTNode, ...]) -> str | None:
    for annotation in annotation_nodes(annotations):
        if annotation.name.text != "error":
            continue
        for arg in annotation.args:
            if isinstance(arg, StringLiteralNode):
                return arg.value
        return "annotated overload is unavailable"
    return None


def annotation_warning_message(annotations: tuple[ASTNode, ...]) -> str | None:
    for annotation in annotation_nodes(annotations):
        if annotation.name.text not in {"warn", "deprecated"}:
            continue
        for arg in annotation.args:
            if isinstance(arg, StringLiteralNode):
                return arg.value
        if annotation.name.text == "deprecated":
            return "selected overload is deprecated"
        return "selected overload has a warning"
    return None


def valid_element_annotations(annotations: tuple[ASTNode, ...]) -> bool:
    return (
        DEFAULT_REGISTRY.validate(annotations, "element", ElementNode(Symbol("_")))
        == ()
    )


def annotated_element_returns(
    node: ElementNode,
    returns: tuple[T.Type, ...],
) -> tuple[T.Type, ...]:
    if has_annotation(node.annotations, "@@tupled"):
        return (T.Tup(*returns),)
    return returns


def commutative_overloads(overload: T.Overload) -> tuple[T.Overload, ...]:
    if len(overload.params) < 2:
        return ()
    generated: list[T.Overload] = []
    seen: set[tuple[T.Type, ...]] = {overload.params}
    names = overload.param_names or tuple(None for _ in overload.params)
    for order in permutations(range(len(overload.params))):
        if order == tuple(range(len(overload.params))):
            continue
        params = tuple(overload.params[index] for index in order)
        if params in seen:
            continue
        seen.add(params)
        generated.append(
            T.Overload(
                params,
                overload.returns,
                overload.generic_constraints,
                overload.where_clause,
                tuple(names[index] for index in order),
                ("commutative", tuple(order)),
                overload.element_tags,
                overload.annotation_error,
                overload.annotation_warning,
                tuple(overload.param_defaults[index] for index in order)
                if overload.param_defaults
                else (),
            )
        )
    return tuple(generated)


def commutative_overload_typing(
    name: Symbol,
    original: T.Overload,
    generated: T.Overload,
    original_index: int,
) -> FunctionOverloadTyping:
    body: list[TypedNode] = []
    names = original.param_names or tuple(None for _ in original.params)
    for param_name, typ in zip(names, original.params, strict=True):
        if param_name is None:
            continue
        body.append(TypedNode(GetVariableNode(param_name), typ))
    body.append(
        TypedElementNode(
            ElementNode(name),
            _returns_result_type(original.returns),
            T.AppliedOverload(
                original,
                {},
                original.params,
                original.returns,
                original.returns,
                (),
                element_tags=original.element_tags,
            ),
            original_index,
        )
    )
    return FunctionOverloadTyping(
        T.Fn(generated.params, generated.returns, generated.element_tags),
        tuple(body),
        generated,
    )


def recursive_overload(
    node: FunctionNode,
    params: tuple[T.Type, ...],
) -> T.Overload | None:
    if node.returns is None:
        return None
    return T.Overload(
        params,
        node.returns,
        where_clause=node.where_clause,
        param_names=_function_param_names_for_overload(node, params),
        element_tags=node.element_tags,
        annotation_error=annotation_error_message(node.annotations),
        annotation_warning=annotation_warning_message(node.annotations),
        param_defaults=(
            tuple(param.default or None for param in node.params)
            if node.params is not None
            else (None,) * len(params)
        ),
    )


def _returns_result_type(returns: tuple[T.Type, ...]) -> T.Type | None:
    if len(returns) == 1:
        return returns[0]
    return None


def _function_param_names_for_overload(
    node: FunctionNode,
    inputs: tuple[T.Type, ...],
) -> tuple[Symbol | None, ...]:
    if node.params is None:
        return (None,) * len(inputs)
    names = tuple(param.name for param in node.params)
    if len(names) < len(inputs):
        return (None,) * (len(inputs) - len(names)) + names
    return names


def _validate_return_all(
    annotation: AnnotationNode,
    target: str,
    node: ASTNode,
) -> tuple[str, ...]:
    function = node.function if isinstance(node, DefineNode) else node
    if isinstance(function, FunctionNode) and function.returns is not None:
        return ("@returnAll cannot be used with an explicit return type",)
    return ()


def _validate_commutative(
    annotation: AnnotationNode,
    target: str,
    node: ASTNode,
) -> tuple[str, ...]:
    if not isinstance(node, DefineNode):
        return ()
    params = node.function.params or ()
    if any(param.name is None for param in params):
        return ("@commutative requires named parameters",)
    return ()


def _validate_mustcall(
    annotation: AnnotationNode,
    target: str,
    node: ASTNode,
) -> tuple[str, ...]:
    del target, node
    kwargs = dict(annotation.kwargs)
    has_all = Symbol("all") in kwargs
    has_any = Symbol("any") in kwargs
    if has_all == has_any:
        return ("@mustcall requires exactly one of all=[...] or any=[...]",)
    key = Symbol("all") if has_all else Symbol("any")
    value = kwargs[key]
    if not isinstance(value, ListLiteralNode):
        return ("@mustcall expects a list literal of method names",)
    for item in value.items:
        if len(item) != 1 or not isinstance(item[0], StringLiteralNode):
            return ("@mustcall method names must be string literals",)
    return ()


def _self_transform(
    function: FunctionNode,
    annotations: tuple[AnnotationNode, ...],
) -> FunctionNode:
    if not has_annotation(annotations, "self") or not function.params:
        return function
    self_param = function.params[0]
    if self_param.name != Symbol("self"):
        return function
    body = (*function.body, GetVariableNode(Symbol("self"), location=function.location))
    returns = (
        (*function.returns, self_param.typ)
        if function.returns is not None and self_param.typ is not None
        else function.returns
    )
    return replace(function, body=body, returns=returns)


def _error_overload_transform(
    overload: T.Overload,
    annotations: tuple[AnnotationNode, ...],
) -> T.Overload:
    message = annotation_error_message(annotations)
    if message is None:
        return overload
    return replace(overload, annotation_error=message)


def _warning_overload_transform(
    overload: T.Overload,
    annotations: tuple[AnnotationNode, ...],
) -> T.Overload:
    message = annotation_warning_message(annotations)
    if message is None:
        return overload
    return replace(overload, annotation_warning=message)


def _err_type_object_transform(
    node: ObjectNode,
    annotations: tuple[AnnotationNode, ...],
) -> ObjectNode:
    if not has_annotation(annotations, "errType"):
        return node
    if node.kind.text == "object":
        fields = _ensure_message_field(node.fields)
        definitions = _ensure_message_definition(node.definitions)
        return replace(node, fields=fields, definitions=definitions)
    if node.kind.text == "variant":
        variants = tuple(
            replace(member, fields=_ensure_message_field(member.fields))
            for member in node.variants
        )
        return replace(node, variants=variants)
    return node


def _ensure_message_field(
    fields: tuple[ObjectFieldNode, ...],
) -> tuple[ObjectFieldNode, ...]:
    if any(field.name == Symbol("message") for field in fields):
        return fields
    return (
        *fields,
        ObjectFieldNode(Symbol("message"), String, access=Symbol("readable")),
    )


def _ensure_message_definition(
    definitions: tuple[DefineNode, ...],
) -> tuple[DefineNode, ...]:
    if any(definition.name == Symbol("message") for definition in definitions):
        return definitions
    return (
        *definitions,
        DefineNode(
            Symbol("message"),
            FunctionNode(
                body=(FieldAccessNode(Symbol("message")),),
                returns=(String,),
            ),
        ),
    )


def _install_builtin_annotations() -> None:
    register_annotation(AnnotationSpec("recursive", frozenset({"define", "fn"})))
    register_annotation(
        AnnotationSpec(
            "self",
            frozenset({"define"}),
            transform_function=_self_transform,
        )
    )
    register_annotation(
        AnnotationSpec(
            "error",
            frozenset({"define"}),
            transform_overload=_error_overload_transform,
        )
    )
    register_annotation(
        AnnotationSpec(
            "warn",
            frozenset({"define"}),
            transform_overload=_warning_overload_transform,
        )
    )
    register_annotation(
        AnnotationSpec(
            "deprecated",
            frozenset({"define"}),
            transform_overload=_warning_overload_transform,
        )
    )
    register_annotation(
        AnnotationSpec(
            "returnAll",
            frozenset({"define", "fn"}),
            validate=_validate_return_all,
        )
    )
    register_annotation(
        AnnotationSpec(
            "commutative",
            frozenset({"define"}),
            validate=_validate_commutative,
        )
    )
    register_annotation(
        AnnotationSpec(
            "mustcall",
            frozenset({"object", "variant"}),
            validate=_validate_mustcall,
        )
    )
    register_annotation(
        AnnotationSpec(
            "errType",
            frozenset({"object", "variant"}),
            transform_object=_err_type_object_transform,
        )
    )
    register_annotation(AnnotationSpec("@@tupled", frozenset({"element"})))


_install_builtin_annotations()
