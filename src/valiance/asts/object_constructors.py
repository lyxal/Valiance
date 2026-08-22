"""Shared AST helpers for explicit object constructors."""

from __future__ import annotations

from dataclasses import replace

from valiance.asts import (
    ASTNode,
    AssertNode,
    AtNode,
    DefineNode,
    FieldAccessNode,
    FieldSetNode,
    ForNode,
    GetVariableNode,
    IfNode,
    MatchNode,
    SetVariableNode,
    StackShuffleNode,
    TryNode,
    UnfoldNode,
    WhileNode,
)
from valiance.vtypes.symbols import Symbol

_SELF = Symbol("self")


def constructor_definitions(
    owner: Symbol,
    definitions: tuple[DefineNode, ...],
) -> tuple[DefineNode, ...]:
    """Return definitions whose local name matches the owning object."""
    return tuple(
        definition
        for definition in definitions
        if definition.name.text == owner.text
    )


def prepare_constructor_body(body: tuple[ASTNode, ...]) -> tuple[ASTNode, ...]:
    """Make direct ``$self.field = ...`` writes update the local receiver."""
    transformed: list[ASTNode] = []
    for index, node in enumerate(body):
        targets_self = isinstance(node, FieldSetNode) and _field_set_targets_self(
            body,
            index,
        )
        already_rebound = (
            targets_self
            and index + 1 < len(body)
            and isinstance(body[index + 1], SetVariableNode)
            and body[index + 1].name == _SELF
        )
        if (
            targets_self
            and not already_rebound
            and _field_set_reads_target(body, index)
        ):
            transformed.extend(
                (
                    GetVariableNode(_SELF, location=node.location),
                    StackShuffleNode(
                        Symbol("move"),
                        (Symbol("value"), Symbol("receiver")),
                        (Symbol("receiver"), Symbol("value")),
                        location=node.location,
                    ),
                )
            )
        transformed.append(_prepare_constructor_child(node))
        if targets_self and not already_rebound:
            transformed.append(SetVariableNode(_SELF, location=node.location))
    return tuple(transformed)


def constructor_initialization_flow(
    body: tuple[ASTNode, ...],
    initialized: frozenset[Symbol],
) -> tuple[frozenset[Symbol], bool]:
    """Return definite fields and whether the constructor path can continue."""
    from valiance.asts import ElementNode

    current = set(initialized)
    index = 0
    while index < len(body):
        node = body[index]
        if (
            isinstance(node, FieldSetNode)
            and index + 1 < len(body)
            and isinstance(body[index + 1], SetVariableNode)
            and body[index + 1].name == _SELF
        ):
            if not _field_set_reads_target(body, index):
                current.add(node.name)
            index += 2
            continue
        if isinstance(node, ElementNode) and node.name == Symbol("panic"):
            return frozenset(current), False
        if isinstance(node, IfNode):
            condition_fields, condition_continues = constructor_initialization_flow(
                node.condition, frozenset(current)
            )
            if not condition_continues:
                return condition_fields, False
            then_flow = constructor_initialization_flow(
                node.then_branch, condition_fields
            )
            else_flow = (
                constructor_initialization_flow(node.else_branch, condition_fields)
                if node.else_branch
                else (condition_fields, True)
            )
            continuing = [fields for fields, continues in (then_flow, else_flow) if continues]
            if not continuing:
                return condition_fields, False
            current = set.intersection(*(set(fields) for fields in continuing))
        elif isinstance(node, MatchNode) and node.cases:
            flows = tuple(
                constructor_initialization_flow(case.body, frozenset(current))
                for case in node.cases
            )
            continuing = [fields for fields, continues in flows if continues]
            if not continuing:
                return frozenset(current), False
            current = set.intersection(*(set(fields) for fields in continuing))
        elif isinstance(node, TryNode):
            body_fields, body_continues = constructor_initialization_flow(
                node.body, frozenset(current)
            )
            # A reached handler terminates the containing function after the
            # handler finishes. Only normal completion of the try body can
            # continue with statements following the try expression.
            if not body_continues:
                return body_fields, False
            current = set(body_fields)
        elif isinstance(node, WhileNode):
            condition_fields, condition_continues = constructor_initialization_flow(
                node.condition, frozenset(current)
            )
            if not condition_continues:
                return condition_fields, False
            current = set(condition_fields)
        elif isinstance(node, AssertNode):
            condition_fields, condition_continues = constructor_initialization_flow(
                node.condition, frozenset(current)
            )
            if not condition_continues:
                return condition_fields, False
            current = set(condition_fields)
        elif isinstance(node, AtNode):
            fields, continues = constructor_initialization_flow(
                node.body, frozenset(current)
            )
            if not continues:
                return fields, False
            current = set(fields)
        index += 1
    return frozenset(current), True


def definitely_initialized_fields(
    body: tuple[ASTNode, ...],
    initialized: frozenset[Symbol],
) -> frozenset[Symbol]:
    """Compute fields initialized on every normally continuing constructor path."""
    fields, _ = constructor_initialization_flow(body, initialized)
    return fields


def _prepare_constructor_child(node: ASTNode) -> ASTNode:
    """Prepare constructor child while analysing object construction."""
    if isinstance(node, IfNode):
        return replace(
            node,
            condition=prepare_constructor_body(node.condition),
            then_branch=prepare_constructor_body(node.then_branch),
            else_branch=prepare_constructor_body(node.else_branch),
        )
    if isinstance(node, AssertNode):
        return replace(
            node,
            condition=prepare_constructor_body(node.condition),
            else_branch=prepare_constructor_body(node.else_branch),
        )
    if isinstance(node, MatchNode):
        return replace(
            node,
            cases=tuple(
                replace(case, body=prepare_constructor_body(case.body))
                for case in node.cases
            ),
        )
    if isinstance(node, TryNode):
        return replace(
            node,
            body=prepare_constructor_body(node.body),
            handlers=tuple(
                replace(handler, body=prepare_constructor_body(handler.body))
                for handler in node.handlers
            ),
        )
    if isinstance(node, WhileNode):
        return replace(
            node,
            condition=prepare_constructor_body(node.condition),
            body=prepare_constructor_body(node.body),
        )
    if isinstance(node, UnfoldNode):
        return replace(
            node,
            condition=prepare_constructor_body(node.condition),
            body=prepare_constructor_body(node.body),
        )
    if isinstance(node, ForNode):
        return replace(node, body=prepare_constructor_body(node.body))
    if isinstance(node, AtNode):
        return replace(node, body=prepare_constructor_body(node.body))
    return node


def _field_set_targets_self(body: tuple[ASTNode, ...], index: int) -> bool:
    """Return the Boolean result of field set targets self while analysing object construction."""
    target = body[index]
    if not isinstance(target, FieldSetNode) or target.location is None:
        return False
    for candidate in reversed(body[:index]):
        if candidate.location != target.location:
            continue
        if isinstance(candidate, GetVariableNode):
            return candidate.name == _SELF
    return False


def _field_set_reads_target(body: tuple[ASTNode, ...], index: int) -> bool:
    """Return whether a direct self write is an augmented assignment."""
    target = body[index]
    if not isinstance(target, FieldSetNode) or target.location is None:
        return False
    return any(
        isinstance(candidate, FieldAccessNode)
        and candidate.name == target.name
        and candidate.location == target.location
        for candidate in body[:index]
    )


def constructor_self_escape_violations(
    body: tuple[ASTNode, ...],
) -> tuple[tuple[str, ASTNode], ...]:
    """Return uses that could expose a constructor receiver before completion.

    The check follows Valiance's source stack: a read of ``$self`` remains
    construction-only until a field access extracts an ordinary value or a
    field write reconstructs and rebinds the receiver.  Any other consumer,
    alias, aggregate, closure, or explicit return is an escape.
    """
    from dataclasses import fields, is_dataclass
    from valiance.asts import (
        ArrayLiteralNode, CallNode, DictLiteralNode, ElementNode, FunctionNode,
        IndexAccessNode, IndexSetNode, IndexUpdateNode, ListLiteralNode,
        RecordLiteralNode, ReturnNode, SetVariablesNode, TupleLiteralNode,
    )

    aggregate_nodes = (
        ArrayLiteralNode, DictLiteralNode, ListLiteralNode,
        RecordLiteralNode, TupleLiteralNode,
    )
    violations: list[tuple[str, ASTNode]] = []

    def contains_self(value: object) -> bool:
        """Return whether a nested source value reads the construction receiver."""
        if isinstance(value, GetVariableNode):
            return value.name == _SELF
        if is_dataclass(value) and not isinstance(value, type):
            return any(
                contains_self(getattr(value, item.name))
                for item in fields(value)
                if item.name != "location"
            )
        if isinstance(value, (tuple, list)):
            return any(contains_self(item) for item in value)
        if isinstance(value, dict):
            return any(contains_self(item) for item in value.values())
        return False

    def inspect(nodes: tuple[ASTNode, ...]) -> None:
        """Follow constructor-only values through one source-stack block."""
        stack: list[bool] = []
        for node in nodes:
            if isinstance(node, GetVariableNode):
                stack.append(node.name == _SELF)
                continue
            if isinstance(node, StackShuffleNode):
                count = len(node.prestack)
                segment = stack[-count:] if count <= len(stack) else [False] * count
                if count <= len(stack):
                    del stack[-count:]
                positions = {name: segment[index] for index, name in enumerate(node.prestack)}
                stack.extend(positions.get(name, False) for name in node.poststack)
                continue
            if isinstance(node, FieldAccessNode):
                if stack:
                    stack.pop()
                stack.append(False)
                continue
            if isinstance(node, FieldSetNode):
                value = stack.pop() if stack else False
                receiver = stack.pop() if stack else False
                if value:
                    violations.append(("constructor receiver cannot be stored in a member", node))
                stack.append(receiver)
                continue
            if isinstance(node, (IndexAccessNode, IndexSetNode, IndexUpdateNode)):
                if any(stack):
                    violations.append(("constructor receiver cannot be used through indexing", node))
                stack.clear()
                stack.append(False)
                continue
            if isinstance(node, SetVariableNode):
                borrowed = stack.pop() if stack else False
                if borrowed and node.name != _SELF:
                    violations.append(("constructor receiver cannot be assigned to another binding", node))
                continue
            if isinstance(node, SetVariablesNode):
                borrowed = any(stack[-len(node.targets):]) if node.targets else False
                if borrowed:
                    violations.append(("constructor receiver cannot be assigned to another binding", node))
                if node.targets:
                    del stack[-len(node.targets):]
                continue
            if isinstance(node, ReturnNode) and contains_self(node):
                violations.append(("constructor receiver cannot be returned explicitly", node))
                continue
            if isinstance(node, FunctionNode) and contains_self(node):
                violations.append(("constructor receiver cannot be captured by a closure", node))
                stack.append(False)
                continue
            if isinstance(node, aggregate_nodes) and contains_self(node):
                violations.append(("constructor receiver cannot be stored in an aggregate", node))
                stack.append(False)
                continue
            if isinstance(node, CallNode) and contains_self(node.args):
                violations.append(("constructor receiver cannot be passed to a function", node))
                stack.append(False)
                continue
            if isinstance(node, ElementNode):
                if contains_self(node.call_args) or any(stack):
                    violations.append(("constructor receiver cannot be passed to an element", node))
                # Element arity is resolved later. Once a construction receiver
                # reaches a call, report it and prevent cascaded escape reports.
                stack.clear()
                stack.append(False)
                continue

            # Inspect every structured child body independently. Constructor
            # field preparation has already made write/rebind sequences explicit.
            if is_dataclass(node):
                for item in fields(node):
                    if item.name == "location":
                        continue
                    value = getattr(node, item.name)
                    if isinstance(value, tuple) and value and all(
                        isinstance(child, ASTNode) for child in value
                    ):
                        inspect(value)
                    elif contains_self(value):
                        violations.append(("constructor receiver cannot escape its constructor", node))
            stack.append(False)

    inspect(body)
    seen: set[tuple[str, object | None]] = set()
    result: list[tuple[str, ASTNode]] = []
    for message, node in violations:
        key = message, node.location
        if key not in seen:
            seen.add(key)
            result.append((message, node))
    return tuple(result)


def constructor_uninitialized_read_violations(
    body: tuple[ASTNode, ...],
    initialized: frozenset[Symbol],
) -> tuple[tuple[str, ASTNode], ...]:
    """Find direct ``$self.member`` reads not definitely initialized at that point."""
    violations: list[tuple[str, ASTNode]] = []

    def inspect(
        nodes: tuple[ASTNode, ...],
        incoming: frozenset[Symbol],
    ) -> tuple[frozenset[Symbol], bool]:
        """Check one block and return its definite fields and continuation state."""
        from valiance.asts import ElementNode

        current = set(incoming)
        stack: list[bool] = []
        index = 0
        while index < len(nodes):
            node = nodes[index]
            if isinstance(node, GetVariableNode):
                stack.append(node.name == _SELF)
            elif isinstance(node, StackShuffleNode):
                count = len(node.prestack)
                segment = stack[-count:] if count <= len(stack) else [False] * count
                if count <= len(stack):
                    del stack[-count:]
                positions = {
                    name: segment[position]
                    for position, name in enumerate(node.prestack)
                }
                stack.extend(positions.get(name, False) for name in node.poststack)
            elif isinstance(node, FieldAccessNode):
                receiver_is_self = stack.pop() if stack else False
                if receiver_is_self and node.name not in current:
                    violations.append(
                        (
                            f"constructor member '{node.name}' may be read before initialization",
                            node,
                        )
                    )
                stack.append(False)
            elif (
                isinstance(node, FieldSetNode)
                and index + 1 < len(nodes)
                and isinstance(nodes[index + 1], SetVariableNode)
                and nodes[index + 1].name == _SELF
            ):
                if stack:
                    stack.pop()
                if stack:
                    stack.pop()
                if not _field_set_reads_target(nodes, index):
                    current.add(node.name)
                stack.append(True)
            elif isinstance(node, SetVariableNode):
                if stack:
                    stack.pop()
            elif isinstance(node, ElementNode) and node.name == Symbol("panic"):
                return frozenset(current), False
            elif isinstance(node, IfNode):
                condition_fields, condition_continues = inspect(
                    node.condition, frozenset(current)
                )
                if not condition_continues:
                    return condition_fields, False
                flows = [inspect(node.then_branch, condition_fields)]
                flows.append(
                    inspect(node.else_branch, condition_fields)
                    if node.else_branch
                    else (condition_fields, True)
                )
                continuing = [fields for fields, continues in flows if continues]
                if not continuing:
                    return condition_fields, False
                current = set.intersection(*(set(fields) for fields in continuing))
                stack.append(False)
            elif isinstance(node, MatchNode) and node.cases:
                flows = tuple(
                    inspect(case.body, frozenset(current)) for case in node.cases
                )
                continuing = [fields for fields, continues in flows if continues]
                if not continuing:
                    return frozenset(current), False
                current = set.intersection(*(set(fields) for fields in continuing))
                stack.append(False)
            elif isinstance(node, TryNode):
                body_fields, body_continues = inspect(
                    node.body, frozenset(current)
                )
                # Handlers are analyzed for invalid member reads, but their
                # state never reaches code after the try: handling returns
                # immediately from the containing function.
                for handler in node.handlers:
                    inspect(handler.body, frozenset(current))
                if not body_continues:
                    return body_fields, False
                current = set(body_fields)
                stack.append(False)
            elif isinstance(node, WhileNode):
                condition_fields, condition_continues = inspect(
                    node.condition, frozenset(current)
                )
                if not condition_continues:
                    return condition_fields, False
                current = set(condition_fields)
                inspect(node.body, frozenset(current))
                stack.append(False)
            elif isinstance(node, AssertNode):
                condition_fields, condition_continues = inspect(
                    node.condition, frozenset(current)
                )
                if not condition_continues:
                    return condition_fields, False
                current = set(condition_fields)
                inspect(node.else_branch, frozenset(current))
                stack.append(False)
            elif isinstance(node, AtNode):
                fields, continues = inspect(node.body, frozenset(current))
                if not continues:
                    return fields, False
                current = set(fields)
                stack.append(False)
            elif isinstance(node, UnfoldNode):
                inspect(node.condition, frozenset(current))
                inspect(node.body, frozenset(current))
                stack.append(False)
            elif isinstance(node, ForNode):
                inspect(node.body, frozenset(current))
                stack.append(False)
            else:
                stack.append(False)
            index += 1
        return frozenset(current), True

    inspect(body, initialized)
    seen: set[tuple[Symbol, object | None]] = set()
    result: list[tuple[str, ASTNode]] = []
    for message, node in violations:
        member = node.name if isinstance(node, FieldAccessNode) else Symbol(message)
        key = member, node.location
        if key not in seen:
            seen.add(key)
            result.append((message, node))
    return tuple(result)


def constructor_handler_violations(
    owner: Symbol,
    body: tuple[ASTNode, ...],
) -> tuple[
    tuple[tuple[str, ASTNode], ...],
    tuple[tuple[str, ASTNode], ...],
]:
    """Validate constructor handlers and report writes whose receiver is discarded."""
    from dataclasses import fields, is_dataclass
    from valiance.asts import ElementNode, ReturnNode

    errors: list[tuple[str, ASTNode]] = []
    warnings: list[tuple[str, ASTNode]] = []

    def handler_terminates(nodes: tuple[ASTNode, ...]) -> bool:
        """Return whether every path through a handler terminates construction."""
        for node in nodes:
            if isinstance(node, ElementNode) and node.name == Symbol("panic"):
                return True
            if isinstance(node, ReturnNode):
                errors.append(
                    (
                        "cannot use 'return' in a constructor handler: completing "
                        f"the handler exits constructor '{owner}' without producing "
                        f"an initialized {owner}; terminate this path with 'panic' instead",
                        node,
                    )
                )
                return True
            if isinstance(node, IfNode):
                if node.else_branch and handler_terminates(
                    node.then_branch
                ) and handler_terminates(node.else_branch):
                    return True
            elif isinstance(node, MatchNode) and node.cases:
                if all(handler_terminates(case.body) for case in node.cases):
                    return True
            elif isinstance(node, TryNode):
                # Following code is reachable only through normal completion of
                # this nested try body. Reached nested handlers return from the
                # containing constructor and therefore terminate this handler.
                if handler_terminates(node.body):
                    return True
            elif isinstance(node, AtNode) and handler_terminates(node.body):
                return True
        return False

    def collect_writes(nodes: tuple[ASTNode, ...]) -> None:
        """Find prepared direct writes to the construction receiver recursively."""
        for index, node in enumerate(nodes):
            if (
                isinstance(node, FieldSetNode)
                and index + 1 < len(nodes)
                and isinstance(nodes[index + 1], SetVariableNode)
                and nodes[index + 1].name == _SELF
            ):
                warnings.append(
                    (
                        f"assignment to '$self.{node.name}' has no effect in this "
                        f"constructor handler: the handler cannot produce the "
                        f"constructed {owner}, so the updated '$self' is discarded; "
                        "remove the assignment or perform the initialization on a "
                        "normally completing constructor path",
                        node,
                    )
                )
            if is_dataclass(node) and not isinstance(node, type):
                for item in fields(node):
                    if item.name == "location":
                        continue
                    value = getattr(node, item.name)
                    if isinstance(value, tuple) and value and all(
                        isinstance(child, ASTNode) for child in value
                    ):
                        collect_writes(value)

    def inspect(nodes: tuple[ASTNode, ...]) -> None:
        """Inspect every try handler nested in one constructor block."""
        for node in nodes:
            if isinstance(node, TryNode):
                for handler in node.handlers:
                    collect_writes(handler.body)
                    if not handler_terminates(handler.body):
                        errors.append(
                            (
                                "constructor handler may complete normally: finishing "
                                f"the handler exits constructor '{owner}' without "
                                f"producing an initialized {owner}; terminate every "
                                "handler path with 'panic'",
                                handler,
                            )
                        )
                    inspect(handler.body)
                inspect(node.body)
                continue
            if is_dataclass(node) and not isinstance(node, type):
                for item in fields(node):
                    if item.name == "location":
                        continue
                    value = getattr(node, item.name)
                    if isinstance(value, tuple) and value and all(
                        isinstance(child, ASTNode) for child in value
                    ):
                        inspect(value)

    inspect(body)

    def deduplicate(
        findings: list[tuple[str, ASTNode]],
    ) -> tuple[tuple[str, ASTNode], ...]:
        """Preserve one finding for each message and source location."""
        seen: set[tuple[str, object | None]] = set()
        result: list[tuple[str, ASTNode]] = []
        for message, node in findings:
            key = message, node.location
            if key not in seen:
                seen.add(key)
                result.append((message, node))
        return tuple(result)

    return deduplicate(errors), deduplicate(warnings)
