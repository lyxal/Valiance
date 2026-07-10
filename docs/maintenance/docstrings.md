# Production docstring policy

Every Python module, function, method, and nested helper under `src/valiance`
should have a non-empty docstring. The requirement is enforced by
`tests/test_docstring_coverage.py`.

The purpose is maintenance, not documentation volume. A useful docstring tells
a reader what responsibility belongs to the function and, for non-obvious code,
which invariant or stage boundary it protects.

## Preferred style

Use a short imperative or return-oriented first sentence:

```python
def compile_program(nodes: list[TypedNode]) -> Program:
    """Compile typed AST nodes into an executable bytecode program."""
```

For complex helpers, add the detail that is not obvious from the signature:

```python
def source_arguments(...):
    """Source arguments in parameter order without losing physical stack order.

    Missing values may come from inferred or cycled function inputs according
    to the branch input mode. The returned branch has consumed only physical
    stack values.
    """
```

## What to document

Prioritise these facts:

- the stage responsibility: parse, analyse, lower, serialize, or execute;
- ordering rules;
- mutation versus reconstruction;
- ownership or retain/release behaviour;
- whether a helper emits diagnostics or returns failure information;
- branch-local versus global state;
- runtime-only versus statically selected dispatch; and
- compatibility constraints such as bytecode format or public CLI behaviour.

## What to avoid

Do not merely repeat the function name:

```python
# Weak
def _resolve_overload(...):
    """Resolve overload."""
```

Explain the decision or output:

```python
# Better
def _resolve_overload(...):
    """Select the most specific applicable overload for the sourced arguments."""
```

Avoid promises that the implementation does not enforce, and do not duplicate
parameter types already present in annotations unless a parameter has unusual
semantics.

## Public and private functions

Public APIs should explain inputs, outputs, raised exceptions, and persistent
side effects when those are not obvious. Private helpers may use a single
sentence, but the sentence should still identify the useful result or invariant.
Nested helpers also need docstrings because they often contain the hardest
recursive or backtracking logic.

Protocol methods such as `__iter__`, `__hash__`, and `__repr__` may be concise.
Their docstrings should describe the Valiance-specific value being iterated,
hashed, or represented.

## Updating docstrings with code

A docstring is part of the implementation contract. Update it when a function
changes responsibility. If a function becomes difficult to describe in one
coherent paragraph, that is often a sign that it owns more than one concern and
should be split.

Run the coverage check directly with:

```powershell
uv run python -m unittest tests.test_docstring_coverage -v
```
