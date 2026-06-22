# Analyser Extension Guide

This guide explains how to add new AST nodes to the analyser and how those
nodes should interact with stack-state inference.

The analyser is deliberately small. It does not own parsing, syntax lowering,
or the whole compiler pipeline. Its job is to take AST nodes, an `Environment`,
and an input stack state, then return typed AST nodes and output stack states.

The implementation is centred on an `Analyser` session object. The session owns
the current `Environment` and the current inference mode, so individual node
handlers do not need to keep threading `env` and `infer_missing` through every
call.

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

## The Session

Create an analyser when you want to analyse a top-level program or a nested
block:

```python
analyser = Analyser(env)
typed_program = analyser.analyse(program)
```

For function literals, the analyser creates a child session with a child
environment frame:

```python
function_analyser = Analyser(function_env, infer_missing=infer_params)
final_branches = function_analyser.typed_block(
    node.body,
    {AnalysisBranch(initial_state)},
)
```

That means most node helpers only need `self`, `node`, and `state`.

## The Dispatcher

Add new node behaviour in `Analyser.node_results`. That method is the main
dispatcher for AST node analysis.

```python
def node_results(self, node: ASTNode, state: AnalysisState) -> set[NodeAnalysis]:
    match node:
        case NumberLiteralNode(_):
            ...
        case ElementNode():
            ...
        case FunctionNode():
            ...
        case IfNode():
            return self._if(node, state)
        case WhileNode():
            return self._while(node, state)
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


def _string_literal(
    self,
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
    return self._string_literal(node, state)
```

This kind of node normally does not care about `infer_missing`, because it never
consumes missing stack values.

## Element-Like Nodes

Element calls are inference-sensitive. In ordinary checking, a call must match
the current stack. During function literal inference, missing inputs may become
function parameters.

The current element analyser follows this shape:

```python
if self.infer_missing:
    applications = apply_overload_candidates_to_stack(
        overloads,
        state.stack,
        self.env.context,
        infer_missing=True,
    )
    return one NodeAnalysis for each viable application

result = self.env.apply(name, state.stack, infer_missing=False)
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
def analyse_function_details(self, node: FunctionNode):
    function_env = self.env.child_scope()
    ...
    final_branches = Analyser(
        function_env,
        infer_missing=infer_params,
    ).typed_block(
        node.body,
        {AnalysisBranch(initial_state)},
    )
```

Nodes such as `IfNode` and `WhileNode` should normally keep using the
environment frame they were given. That lets them write variables in the current
function scope instead of creating a new function-local scope.

For short-lived bindings such as loop variables, use
`env.define_temporary_variable(name, typ)` and then
`env.drop_local_variable(name)`. Temporary variables cannot replace an existing
local binding; choose a fresh loop variable name or report a compile error.

```python
self.env.define_temporary_variable("item", Number)
body_branches = self.typed_block(body, branches)
self.env.drop_local_variable("item")

self.env.lookup_local_variable("item")
# None
```

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

Use `Analyser.block` when you only need final stack states.

```python
final_states = analyser.block(
    body_nodes,
    {initial_state},
)
```

Use `Analyser.typed_block` when you also need the typed body nodes.

```python
branches = analyser.typed_block(
    body_nodes,
    {AnalysisBranch(initial_state)},
)

for branch in branches:
    branch.state
    branch.typed_body
```

Control-flow nodes and function literals usually need `typed_block`.
Tiny stack-only checks can use `block`.

For conditions, use `condition_branches`. It analyses the condition block with
the current inference mode, requires a control type such as `Bool`, pops that
control value, and returns branch inputs ready for the body.

```python
condition_inputs = self.condition_branches(node.condition, state, Bool)
```

So a condition can still contribute inferred function inputs. The analyser just
hides the repetitive "check top of stack, then pop Bool" ceremony.

Conditions are all-or-nothing. If analysis finds several possible condition
paths, every surviving path must leave a non-`Never` value assignable to the
control type. If one path leaves `Bool` and another leaves `Number`, the whole
condition is rejected. Filtering out the bad path would make the control-flow
node unsound.

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


def _if(
    self,
    node: IfNode,
    state: AnalysisState,
) -> set[NodeAnalysis]:
    results: set[NodeAnalysis] = set()
    condition_inputs = self.condition_branches(node.condition, state, Bool)
    if not condition_inputs:
        error("if condition must leave Bool on the stack")
        return results

    for condition in condition_inputs:
        then_branches = self.typed_block(node.then_body, {condition})
        else_branches = self.typed_block(node.else_body, {condition})

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
                    condition=condition.typed_body,
                    then_body=left.typed_body,
                    else_body=right.typed_body,
                )
                results.add(NodeAnalysis(typed_if, merged_state))

    return results
```

The exact `TypedIfNode` shape is up to your AST model. The important part is
that the branch bodies remain typed.

The condition's `Bool` is not a normal result. It is a control value.
`condition_branches` pops it before analysing branch bodies. If the condition
contains overloaded or inference-sensitive code, those inferred inputs are
preserved in the returned branch inputs.

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
def _while(
    self,
    node: WhileNode,
    state: AnalysisState,
) -> set[NodeAnalysis]:
    results: set[NodeAnalysis] = set()
    condition_inputs = self.condition_branches(node.condition, state, Bool)
    if not condition_inputs:
        error("while condition must leave Bool on the stack")
        return results

    for condition in condition_inputs:
        body_outputs = self.typed_block(
            node.body,
            {condition},
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
                condition=condition.typed_body,
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

## ForEach Nodes

A `ForEachNode` usually needs two special behaviours:

1. derive the item type from the iterable collection
2. bind a loop variable only for the loop body

Possible AST shape:

```python
@dataclass(frozen=True)
class ForEachNode(ASTNode):
    item_name: str
    iterable: tuple[ASTNode, ...]
    body: tuple[ASTNode, ...]
```

Use `collection_item_type` to peel one collection rank:

```python
collection_item_type(Number+)
# Number

collection_item_type(Number++)
# Number+
```

Minimum/rugged collection ranks may produce a union because peeling all known
rank can leave either an atomic item or another collection at runtime.

Sketch:

```python
def _foreach(
    self,
    node: ForEachNode,
    state: AnalysisState,
) -> set[NodeAnalysis]:
    iterable_branches = self.typed_block(
        node.iterable,
        {AnalysisBranch(state)},
    )

    results: set[NodeAnalysis] = set()
    for branch in iterable_branches:
        stack = branch.state.stack
        if not stack:
            error("foreach iterable must leave a collection on the stack")
            continue

        item_type = collection_item_type(stack[-1])
        if item_type is None:
            error("foreach requires a collection")
            continue

        body_state = AnalysisState(
            branch.state.inputs,
            TypeStack(stack.items[:-1]),
        )

        self.env.define_temporary_variable(node.item_name, item_type)
        body_branches = self.typed_block(
            node.body,
            {AnalysisBranch(body_state)},
        )
        self.env.drop_local_variable(node.item_name)

        for body_branch in body_branches:
            if body_branch.state.inputs != branch.state.inputs:
                error("foreach body inferred different function inputs")
                continue

            merged_state = AnalysisState(
                branch.state.inputs,
                merge_stacks(body_state.stack, body_branch.state.stack),
            )

            typed_foreach = TypedForEachNode(
                node=node,
                typ=None,
                iterable=branch.typed_body,
                body=body_branch.typed_body,
                item_type=item_type,
            )
            results.add(NodeAnalysis(typed_foreach, merged_state))

    return results
```

That merge treats the loop as zero-or-more iterations. The loop variable is
visible while the body is analysed, and gone afterwards. Because the same
environment frame is used, body nodes can still write to variables in the
current function scope.

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

Nested node helpers use the same analyser session, so they automatically
preserve `self.infer_missing`. Create a child `Analyser` only when the language
semantics really enter a new inference mode, such as a function literal.

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
3. Add a `case` in `Analyser.node_results`.
4. Write an `Analyser` method returning `set[NodeAnalysis]`.
5. Decide whether the node pushes, pops, merges, or only validates the stack.
6. Use `self.typed_block` for nested bodies.
7. Preserve typed child nodes with `typed_block` when needed.
8. Add tests for ordinary checking and function-literal inference.
