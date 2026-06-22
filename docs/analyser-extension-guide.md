# Analyser Extension Guide

This guide explains how to add new AST nodes to the analyser and how those
nodes should interact with stack-state inference.

The analyser is deliberately small. It does not own parsing, syntax lowering,
or the whole compiler pipeline. Its job is to take AST nodes, an `Environment`,
and an input stack state, then return typed AST nodes and output stack states.

## Core Records

The analyser works with three small records.

```python
@dataclass(frozen=True)
class AnalysisState:
    inputs: tuple[Type, ...]
    stack: TypeStack
```

`AnalysisState.stack` is the current type stack at this point in the program.
`AnalysisState.inputs` is the list of function inputs inferred so far while
checking a function literal with omitted parameters.

```python
@dataclass(frozen=True)
class NodeAnalysis:
    typed_node: TypedNode
    state: AnalysisState | None
```

`NodeAnalysis` is the result of analysing one AST node on one possible path.
`state=None` means that path is dead or invalid. The `typed_node` should still
record what was known about the node when useful.

```python
@dataclass(frozen=True)
class AnalysisBranch:
    state: AnalysisState
    typed_body: tuple[TypedNode, ...] = ()
```

`AnalysisBranch` is used inside block and function analysis. It carries both
the current stack state and the typed AST nodes produced along that path.

## The Dispatcher

Add new node behaviour in `_analyse_node_results`. That function is the main
dispatcher for AST node analysis.

```python
def _analyse_node_results(
    node: ASTNode,
    state: AnalysisState,
    env: Environment,
    *,
    infer_missing: bool,
) -> set[NodeAnalysis]:
    match node:
        case NumberLiteralNode(_):
            ...
        case ElementNode():
            ...
        case FunctionNode():
            ...
        case IfNode():
            return _analyse_if(node, state, env, infer_missing=infer_missing)
        case WhileNode():
            return _analyse_while(node, state, env, infer_missing=infer_missing)
        case _:
            return {NodeAnalysis(TypedNode(node, None), state)}
```

The return type is a set because one AST node may produce multiple possible
states. This happens during function inference and control-flow analysis.

## Simple Value Nodes

A literal usually pushes one known type onto the stack and returns one
`NodeAnalysis`.

```python
String = N("String")


def _analyse_string_literal(
    node: StringLiteralNode,
    state: AnalysisState,
) -> set[NodeAnalysis]:
    next_state = AnalysisState(
        state.inputs,
        state.stack.push(String),
    )
    return {NodeAnalysis(TypedNode(node, String), next_state)}
```

Then add the dispatcher case:

```python
case StringLiteralNode():
    return _analyse_string_literal(node, state)
```

This kind of node normally does not care about `infer_missing`, because it never
consumes missing stack values.

## Element-Like Nodes

Element calls are inference-sensitive. In ordinary checking, a call must match
the current stack. During function literal inference, missing inputs may become
function parameters.

The current element analyser follows this shape:

```python
if infer_missing:
    applications = apply_overload_candidates_to_stack(
        overloads,
        state.stack,
        env.context,
        infer_missing=True,
    )
    return one NodeAnalysis for each viable application

result = env.apply(name, state.stack, infer_missing=False)
return one NodeAnalysis for the chosen application or failed known application
```

This is why `fn => + end` can infer both:

```text
Function[Number, Number -> Number]
Function[String, String -> String]
```

When adding a new callable or stack-consuming node, decide whether it should use
the same `infer_missing` behaviour. Most ordinary element-like nodes should.

## Variable Scope

Variables are scoped through `Environment` frames.

Use `env.lookup_variable(name)` for reads. It checks the current frame first,
then walks outward through parent frames.

Use `env.define_variable(name, typ)` for writes. It writes only to the current
frame.

Use `env.child_scope()` when entering a function literal. The child scope can
read variables, overloads, and object definitions from the outer environment,
but variable writes stay local to the function.

```python
outer = Environment()
outer.define_variable("x", Number)

inner = outer.child_scope()
inner.lookup_variable("x")
# Number

inner.define_variable("x", String)

inner.lookup_variable("x")
# String

outer.lookup_variable("x")
# Number
```

This is the rule you want for function literals:

```python
def analyse_function_details(node: FunctionNode, env: Environment):
    function_env = env.child_scope()
    ...
    final_branches = analyse_typed_block(
        node.body,
        {AnalysisBranch(initial_state)},
        function_env,
        infer_missing=infer_params,
    )
```

Nodes such as `IfNode` and `WhileNode` should normally keep using the
environment frame they were given. That lets them write variables in the current
function scope instead of creating a new function-local scope.

## Typed AST Contract

Always return typed nodes from new analyser helpers.

```python
NodeAnalysis(
    TypedNode(original_node, result_type),
    next_state,
)
```

For nodes that do not have a direct expression type, use `TypedNode(node, None)`
and let the output `AnalysisState` carry the real information.

Function literals are special. `analyse_function_details` returns a
`FunctionAnalysis` that keeps typed bodies for each inferred overload. This is
why an overloaded function literal can preserve branch-specific typed AST:

```python
TypedFunctionNode(
    node=function_node,
    typ=OverloadSet(...),
    overloads=(
        FunctionOverloadTyping(number_function_type, number_typed_body),
        FunctionOverloadTyping(string_function_type, string_typed_body),
    ),
)
```

If your new node contains nested bodies, preserve their typed nodes in the same
spirit. Do not only return the outer node type if later compiler stages need the
typed children.

## Block Analysis

Use `analyse_block` when you only need final stack states.

```python
final_states = analyse_block(
    body_nodes,
    {initial_state},
    env,
    infer_missing=infer_missing,
)
```

Use `analyse_typed_block` when you also need the typed body nodes.

```python
branches = analyse_typed_block(
    body_nodes,
    {AnalysisBranch(initial_state)},
    env,
    infer_missing=infer_missing,
)

for branch in branches:
    branch.state
    branch.typed_body
```

Control-flow nodes and function literals usually need `analyse_typed_block`.
Tiny stack-only checks can use `analyse_block`.

## If Nodes

An `IfNode` should analyse its condition, require a `Bool`, pop that control
value, analyse both branches, then merge surviving branch states.

Possible AST shape:

```python
@dataclass(frozen=True)
class IfNode(ASTNode):
    condition: tuple[ASTNode, ...]
    then_body: tuple[ASTNode, ...]
    else_body: tuple[ASTNode, ...] = ()
```

Helper shape:

```python
Bool = N("Bool")


def _analyse_if(
    node: IfNode,
    state: AnalysisState,
    env: Environment,
    *,
    infer_missing: bool,
) -> set[NodeAnalysis]:
    condition_branches = analyse_typed_block(
        node.condition,
        {AnalysisBranch(state)},
        env,
        infer_missing=infer_missing,
    )

    results: set[NodeAnalysis] = set()
    for branch in condition_branches:
        stack = branch.state.stack
        if not stack or not assignable(stack[-1], Bool, env.context):
            error("if condition must leave Bool on the stack")
            continue

        branch_input = AnalysisBranch(
            AnalysisState(
                branch.state.inputs,
                TypeStack(stack.items[:-1]),
            )
        )

        then_branches = analyse_typed_block(
            node.then_body,
            {branch_input},
            env,
            infer_missing=infer_missing,
        )
        else_branches = analyse_typed_block(
            node.else_body,
            {branch_input},
            env,
            infer_missing=infer_missing,
        )

        for left in then_branches:
            for right in else_branches:
                if left.state.inputs != right.state.inputs:
                    error("branches inferred different function inputs")
                    continue

                merged_state = AnalysisState(
                    left.state.inputs,
                    merge_stacks(left.state.stack, right.state.stack),
                )

                typed_if = TypedIfNode(
                    node=node,
                    typ=None,
                    condition=branch.typed_body,
                    then_body=left.typed_body,
                    else_body=right.typed_body,
                )
                results.add(NodeAnalysis(typed_if, merged_state))

    return results
```

The exact `TypedIfNode` shape is up to your AST model. The important part is
that the branch bodies remain typed.

The condition's `Bool` is not a normal result. It is a control value. Pop it
before analysing branch bodies.

## While Nodes

A `WhileNode` is not just "condition once, body once". The body may run zero or
more times, so the analyser should check that one iteration is valid and merge
the before-loop and after-body states.

Possible AST shape:

```python
@dataclass(frozen=True)
class WhileNode(ASTNode):
    condition: tuple[ASTNode, ...]
    body: tuple[ASTNode, ...]
```

Simple first-pass helper:

```python
def _analyse_while(
    node: WhileNode,
    state: AnalysisState,
    env: Environment,
    *,
    infer_missing: bool,
) -> set[NodeAnalysis]:
    condition_branches = analyse_typed_block(
        node.condition,
        {AnalysisBranch(state)},
        env,
        infer_missing=infer_missing,
    )

    results: set[NodeAnalysis] = set()
    for branch in condition_branches:
        stack = branch.state.stack
        if not stack or not assignable(stack[-1], Bool, env.context):
            error("while condition must leave Bool on the stack")
            continue

        body_input = AnalysisBranch(
            AnalysisState(
                branch.state.inputs,
                TypeStack(stack.items[:-1]),
            )
        )

        body_outputs = analyse_typed_block(
            node.body,
            {body_input},
            env,
            infer_missing=infer_missing,
        )

        for body_output in body_outputs:
            if body_output.state.inputs != state.inputs:
                error("loop body inferred different function inputs")
                continue

            merged_state = AnalysisState(
                state.inputs,
                merge_stacks(state.stack, body_output.state.stack),
            )

            typed_while = TypedWhileNode(
                node=node,
                typ=None,
                condition=branch.typed_body,
                body=body_output.typed_body,
            )
            results.add(NodeAnalysis(typed_while, merged_state))

    return results
```

That sketch is conservative. Later, replace it with a fixed-point loop:

1. Start with the state before the loop.
2. Analyse condition and body from the current approximation.
3. Merge the old approximation with the body output.
4. Repeat until the state stops changing.
5. Report an error if it keeps widening beyond a compiler limit.

## Inference Rules

When analysing inside `fn => ... end`, `infer_missing=True`.

That means:

- missing element inputs can become function parameters
- ambiguous element applications can produce multiple branches
- each branch carries its own typed body
- final branch states become function signatures
- multiple final signatures become an `OverloadSet`

When analysing inside `fn () => ... end`, `infer_missing=False`.

That means:

- missing inputs are errors
- known failed overloads produce `Never`
- invalid paths may still be useful for diagnostics
- inferred signatures with `Never` returns are filtered out when other valid
  signatures exist

Thread `infer_missing` through nested node helpers unless the node has a good
reason to reset inference. Most nodes should preserve it.

## Handling Errors

There are three common error choices:

1. Unknown construct: return `NodeAnalysis(TypedNode(node, None), None)`.
2. Known construct with bad stack types: return a typed node and a state with
   `Never` where the failed value appears.
3. Branch-specific failure: drop only that branch and keep other branches.

Prefer dropping only the bad branch when other branches are still meaningful.
That is what lets overloaded function inference recover from one impossible
candidate.

## Checklist For Adding A Node

1. Add the AST dataclass.
2. Add a typed AST dataclass if the node has typed children.
3. Add a `case` in `_analyse_node_results`.
4. Write a helper returning `set[NodeAnalysis]`.
5. Decide whether the node pushes, pops, merges, or only validates the stack.
6. Thread `infer_missing` into nested analysis.
7. Preserve typed child nodes with `analyse_typed_block` when needed.
8. Add tests for ordinary checking and function-literal inference.
