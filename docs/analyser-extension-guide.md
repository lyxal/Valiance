# Analyser Extension Guide

This guide explains how to add AST nodes to the analyser and how those nodes
interact with inference.

The analyser is branch-centric. There is no separate "normal" analyser and
"inference" analyser. Every block starts with a `BranchSet` and returns a
`BranchSet`; ordinary checking is just the single-branch case.

## Core Records

`Analyser` is the session object. It owns:

- `env`: overloads, object definitions, trait/context facts, and built-ins
- `diagnostics`: session-level errors
- the node dispatcher
- the block driver

`AnalysisBranch` is the main unit of analysis:

```python
@dataclass(frozen=True)
class AnalysisBranch:
    stack: TypeStack
    inputs: tuple[Type, ...]
    variables: BranchVariables
    typed_body: tuple[TypedNode, ...]
    input_mode: InputMode
    cycle_params: tuple[Type, ...]
```

`BranchSet` wraps `frozenset[AnalysisBranch]` and provides the operations new
nodes should compose:

```python
branches = analyser.analyse_block(initial_branches, node.body)
branches = analyser.analyse_node(branches, one_node)
branches = branches.require_stack_top_assignable(Bool, analyser.env.context)
branches = branches.pop_stack_top()
```

## Input Modes

Input sourcing is explicit on each branch:

- `InputMode.TOP_LEVEL`: stack underflow is an error and the branch dies.
- `InputMode.INFER_INPUTS`: missing element inputs become inferred function
  parameters. This is `fn => ...`.
- `InputMode.NILADIC`: explicit empty params, `fn () => ...`; underflow is an
  error.
- `InputMode.CYCLE_EXPLICIT_PARAMS`: explicit non-empty params, such as
  `fn (x: Number, y: Number) => ...`; when the stack underflows, the analyser
  reuses parameter types cyclically.

Input inference and input cycling are mutually exclusive.

Element application always goes through one branch operation:

1. get overload candidates from `env.overloads_for(name)`
2. source arguments from the branch stack, inference, or cycling
3. call `apply_overload`
4. emit one output branch per best viable overload

That is why `fn => +` produces a function overload set, while `fn () => +` is
invalid, and `fn (x: Number, y: Number) => + +` can reuse explicit parameter
types for the second `+`.

## Variables

Variable facts live in `BranchVariables`, not in `Environment`.

`Environment` is for branch-independent facts:

- overloads
- object definitions and attributes
- trait, variant, and unit-tag context
- built-ins

`BranchVariables` tracks:

- function locals
- read-only function parameters
- captured outer variables for reads
- block locals introduced by control-flow or loop bodies

Reads search block locals, then function locals, parameters, and captures:

```python
typ = branch.variables.read("x")
```

Writes return a new variable frame plus an optional diagnostic:

```python
variables, diagnostic = branch.variables.write("x", Number)
if diagnostic is not None:
    error(diagnostic)
branch = branch.with_variables(variables)
```

Writing to a parameter is an error. Writing a captured variable creates a local
shadow in the current function branch. Block-local variables can be introduced
with `with_block_local` and removed with `drop_block_locals`.

At branch joins, merge variables that existed before the control-flow node.
Branch-local names introduced only inside a branch body are dropped.

## Dispatcher Shape

Add node behaviour in `Analyser._analyse_node_from_branch`:

```python
def _analyse_node_from_branch(
    self,
    branch: AnalysisBranch,
    node: ASTNode,
) -> set[AnalysisBranch]:
    match node:
        case NumberLiteralNode(_):
            ...
        case ElementNode():
            return self._element(branch, node)
        case FunctionNode():
            ...
        case IfNode():
            return self._if(branch, node)
        case WhileNode():
            return self._while(branch, node)
        case _:
            return {branch.append_typed(TypedNode(node, None))}
```

Node helpers should accept one `AnalysisBranch` and return zero or more
branches. Nested bodies should call `analyse_block` with a `BranchSet`.

## Simple Value Nodes

A literal pushes a known type and appends a typed node:

```python
def _string_literal(self, branch: AnalysisBranch, node: StringLiteralNode):
    return {
        branch
        .with_stack(branch.stack.push(String))
        .append_typed(TypedNode(node, String))
    }
```

## Function Literals

Function literals create a new branch with a new input mode:

```python
if node.params is None:
    mode = InputMode.INFER_INPUTS
elif not node.params:
    mode = InputMode.NILADIC
else:
    mode = InputMode.CYCLE_EXPLICIT_PARAMS
```

Named parameters are readable through `BranchVariables.parameters` and are
read-only. Outer variables become captures. The function body is analysed as a
normal branch set, and each surviving output branch becomes a function
signature. Multiple signatures become an overload set.

Typed function nodes preserve typed bodies per overload through
`FunctionOverloadTyping`.

## Conditions

Do not write a specialised condition helper for each control-flow node. Use the
general branch-set operations:

```python
condition = analyser.analyse_block(incoming, node.condition)
condition = condition.require_stack_top_assignable(Bool, analyser.env.context)
condition = condition.pop_stack_top()
```

The validation is all-or-nothing. If one condition branch leaves `Bool` and
another leaves `Number`, the whole condition is invalid. Filtering to the Bool
branch would be unsound because the rejected branch was a real possible
execution/type-inference path.

## If Nodes

Suggested AST:

```python
@dataclass(frozen=True)
class IfNode(ASTNode):
    condition: tuple[ASTNode, ...]
    then_body: tuple[ASTNode, ...]
    else_body: tuple[ASTNode, ...] = ()
```

Sketch:

```python
Bool = N("Bool")


def _if(self, branch: AnalysisBranch, node: IfNode) -> set[AnalysisBranch]:
    incoming = BranchSet.one(branch)
    condition = self.analyse_block(incoming, node.condition)
    condition = condition.require_stack_top_assignable(Bool, self.env.context)
    if not condition:
        error("if condition must leave Bool")
        return set()

    body_inputs = condition.pop_stack_top()
    then_outputs = self.analyse_block(body_inputs, node.then_body)
    else_outputs = self.analyse_block(body_inputs, node.else_body)

    outputs: set[AnalysisBranch] = set()
    for left in then_outputs:
        for right in else_outputs:
            if left.inputs != right.inputs:
                error("if branches inferred different inputs")
                continue
            stack = merge_stacks(left.stack, right.stack, self.env.context)
            variables = left.variables.merge_against(
                right.variables,
                branch.variables,
                self.env.context,
            )
            typed_if = TypedIfNode(
                node=node,
                typ=None,
                condition=tuple(...),
                then_body=left.typed_body,
                else_body=right.typed_body,
            )
            outputs.add(
                branch
                .with_stack(stack)
                .with_variables(variables)
                .append_typed(typed_if)
            )
    return outputs
```

Your final typed node shape can differ. The important contract is that typed
child bodies are preserved.

## While Nodes

Suggested AST:

```python
@dataclass(frozen=True)
class WhileNode(ASTNode):
    condition: tuple[ASTNode, ...]
    body: tuple[ASTNode, ...]
```

First-pass flow:

```python
def _while(self, branch: AnalysisBranch, node: WhileNode) -> set[AnalysisBranch]:
    incoming = BranchSet.one(branch)
    condition = self.analyse_block(incoming, node.condition)
    condition = condition.require_stack_top_assignable(Bool, self.env.context)
    if not condition:
        error("while condition must leave Bool")
        return set()

    body_inputs = condition.pop_stack_top()
    body_outputs = self.analyse_block(body_inputs, node.body)

    outputs: set[AnalysisBranch] = {branch}
    for body_output in body_outputs:
        stack = merge_stacks(branch.stack, body_output.stack, self.env.context)
        variables = body_output.variables.merge_against(
            branch.variables,
            branch.variables,
            self.env.context,
        )
        outputs.add(branch.with_stack(stack).with_variables(variables))
    return outputs
```

This models zero-or-more execution. Later, it can become a fixed-point loop
without changing the rest of the analyser architecture.

## ForEach Nodes

Suggested AST:

```python
@dataclass(frozen=True)
class ForEachNode(ASTNode):
    item_name: str
    iterable: tuple[ASTNode, ...]
    body: tuple[ASTNode, ...]
```

Use `collection_item_type` to derive the item type:

```python
iterable_outputs = self.analyse_block(BranchSet.one(branch), node.iterable)

for iterable_branch in iterable_outputs:
    if not iterable_branch.stack:
        error("foreach iterable must leave a collection")
        continue

    item_type = collection_item_type(iterable_branch.stack[-1])
    if item_type is None:
        error("foreach requires a collection")
        continue

    body_branch = iterable_branch.with_stack(
        TypeStack(iterable_branch.stack.items[:-1])
    )
    body_branch = body_branch.with_variables(
        body_branch.variables.with_block_local(node.item_name, item_type)
    )
    body_outputs = self.analyse_block(BranchSet.one(body_branch), node.body)
```

After the body, call `drop_block_locals` before joining. Writes to pre-existing
function locals survive through the join; the loop variable itself does not.

## Error Handling

Use branch dropping when a path is genuinely impossible. Use all-or-nothing
validation for constructs where filtering would be unsound, especially control
conditions.

Common choices:

- unknown element: diagnose and drop the branch
- top-level underflow: diagnose and drop the branch
- niladic function underflow: diagnose and drop the branch
- no viable overload during inference: drop that candidate branch
- mixed valid/invalid condition branches: reject the whole condition set

## Checklist

1. Add the AST dataclass.
2. Add a typed AST dataclass if the node owns child bodies.
3. Add a dispatcher case in `_analyse_node_from_branch`.
4. Make the helper transform one `AnalysisBranch` into a set of branches.
5. Use `analyse_block(BranchSet.one(branch), nested_nodes)` for child blocks.
6. Use `BranchSet` validation operations for control values.
7. Store variables in `BranchVariables`, not `Environment`.
8. Preserve typed child bodies in the typed AST.
9. Add tests for single-branch, overload-branch, and inference cases.
