# Lints and optimisation passes

Valiance lints are non-fatal, actionable diagnostics for source patterns that are
valid but unnecessarily indirect, redundant, or unreachable. They run during
analysis because that is the first stage with both parsed structure and the
resolved type, branch, and overload facts needed to make safe recommendations.

Lint detection is deliberately isolated from the analyser implementation under
`src/valiance/analysis/lints/`. The analyser exposes generic lifecycle hooks;
individual rules register against those hooks and never need to be wired into
`analyser.py` or `handlers/core.py`.

The analyser does not rewrite programs while reporting lints. Optimisation is a
separate post-codegen stage in `runtime/optimizer.py`, so enabling it does not
change diagnostics or analysis decisions. The structured lint representation can
still support future typed rewrites without parsing human-facing messages.

## Package layout

The lint subsystem is split by responsibility:

- `lints/models.py` defines `LintFinding`, `LintRewrite`, `RewriteKind`, and the
  `finding(...)` constructor.
- `lints/contexts.py` defines the immutable context objects supplied to rules.
- `lints/registry.py` owns rule registration and dispatch.
- `lints/rules/` contains built-in rule modules.
- `lints/rules/__init__.py` discovers every non-private module in that directory
  and calls its `register(registry)` function.

Because discovery is automatic, adding a built-in lint normally means adding one
new module under `lints/rules/`. No existing analyser file or rule index needs to
change.

## Public analyser results

An `Analyser` exposes two views of the same findings:

- `analyser.lints` is the backwards-compatible list of rendered strings used by
  the CLI and REPL.
- `analyser.lint_findings` contains `LintFinding` records with a stable rule
  code, raw message, source location, originating AST node, and optional
  `LintRewrite` metadata.

The structured types and registry API are exported from `valiance.analysis`:

```python
from valiance.analysis import (
    Analyser,
    LintFinding,
    LintRegistry,
    LintRewrite,
    NodeLintContext,
    RewriteKind,
    finding,
)
```

`LintRewrite.semantics_preserving` is true for the current rules. Its
`replacement` field is display text only; it is not an executable text edit.
Future tooling should dispatch on `RewriteKind` and inspect the associated AST
node.

Use `analyser.clear_lints()` when reusing an analyser. This keeps the rendered
and structured collections synchronized.

## Current rules

The built-in rule modules cover:

- identity casts and statically safe checked casts;
- `move(...)` operations that leave the stack unchanged;
- `copy(...)` operations with an empty output list;
- code directly following an explicit `return` or `break` in the same block;
- match cases following an unconditional catch-all case;
- repeated all-literal match cases; and
- repeated literal alternatives within an `||` pattern.

The match rules intentionally do not treat guarded or destructuring catch-all
patterns as unconditional, and do not equate repeated guards or arbitrary
expression patterns. Those expressions may be effectful or depend on mutable
state, so removing one is not proven safe merely because the ASTs look alike.

## Adding a lint rule

### 1. Establish the safety argument

Write down why the replacement preserves all observable behaviour, including:

- stack inputs and outputs;
- evaluation order;
- panics and other element tags;
- variable reads and writes;
- imports and declarations;
- runtime dispatch; and
- guard or argument evaluation.

A textual resemblance is not enough. For example, arithmetic identities must
not be added solely by checking an element name such as `+`: a user-defined or
overlaid overload may have effects that the builtin does not.

If safety depends on a selected overload, type, or branch fact, use a context
that runs after that fact has been resolved. If safety cannot be proven, prefer
a warning without rewrite metadata or do not add the rule.

### 2. Add focused tests first

Place focused analyser tests in `tests/test_analyser.py` or registry-focused
tests in `tests/test_lints.py`. Cover:

1. the positive pattern;
2. a nearby pattern that must not be flagged;
3. the exact actionable message;
4. the stable rule code; and
5. the expected `RewriteKind`.

For a rule likely to encounter many syntax combinations, extend the
`smart-diagnostics` target in `tools/fuzzing.py`. Fuzz properties should verify
that lints remain non-fatal, structured and rendered views agree, and advertised
rewrites are marked semantics-preserving.

### 3. Choose a lifecycle context

The registry supports three rule shapes:

- `register_block(rule)` receives `BlockLintContext` once for each lexical block
  and is appropriate for relationships between sibling statements.
- `register_node(NodeType, rule)` receives `NodeLintContext` after one concrete
  AST node has been analysed. It includes the incoming branch, resulting branch
  set, and current environment.
- `register_match(rule)` receives `MatchLintContext` after pattern validity and
  exhaustiveness checks have succeeded, which prevents advice from being emitted
  for malformed matches.

Do not put lint decisions in the parser merely because a pattern is syntactic.
The lint contexts retain access to raw AST and analysis facts while keeping all
non-fatal semantic advice in one subsystem.

### 4. Add one automatically discovered rule module

Create a module in `src/valiance/analysis/lints/rules/`. It must expose a
`register(registry)` function. For example:

```python
from valiance.asts import NumberLiteralNode

from ..contexts import NodeLintContext
from ..models import finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register this module's rules."""
    registry.register_node(NumberLiteralNode, lint_number)


def lint_number(context: NodeLintContext):
    """Report the example number-literal pattern."""
    return (
        finding(
            "example-number",
            "replace this example number with a named constant",
            context.node,
        ),
    )
```

The discovery loader imports the new module on process start. Do not add an
import to an analyser module or edit a central list.

### 5. Emit structured findings

Use `finding(...)` with an actionable message, stable kebab-case code, source
node, and optional rewrite:

```python
return (
    finding(
        "no-op-copy",
        "this copy produces no values and has no effect; remove it",
        context.node,
        rewrite=LintRewrite(RewriteKind.REMOVE_NODE),
    ),
)
```

Codes are API-like identifiers. Keep them stable after release and create a new
code when the meaning materially changes.

Use the narrowest rewrite kind:

- `REMOVE_NODE` for one redundant AST node;
- `REPLACE_NODE` when another construct should replace it;
- `REMOVE_UNREACHABLE_SUFFIX` for the remainder of a block;
- `REMOVE_MATCH_CASE` for one ordered case; and
- `REMOVE_PATTERN_ALTERNATIVE` for one option in an `||` pattern.

Add a new enum member only when none of these accurately describes the
structural action. Do not encode an optimiser algorithm in a lint message.

### 6. Use a custom registry when embedding

`Analyser(lint_registry=registry)` installs an isolated registry for one analysis
session. Child analysers automatically reuse it, so rules also apply inside
nested functions and call-site analysis.

The package-level `DEFAULT_REGISTRY` contains the automatically discovered
built-in rules. Embedders may construct a fresh `LintRegistry` to enable only
selected rules, or register additional rules on their own registry.

### 7. Verify presentation and behaviour

Run the focused analyser tests, `tests/test_lints.py`, the full suite, and the
smart-diagnostics fuzz target. If the message spans lines, add a CLI rendering
test so source arrows and carets remain correct.

## The bytecode optimisation pipeline

`compile_program(typed)` lowers the analysed program and then runs
`DEFAULT_OPTIMIZATION_PIPELINE`. Pass `optimization_pipeline=...` to select a
custom pipeline for that compilation, or `optimize=False` to inspect or preserve
the direct code-generator output. The CLI exposes the opt-out through
`--no-optimize` on `compile` and `run`.

The public extension points are exported from `valiance.runtime`:

```python
from valiance.runtime import OptimizationPipeline, optimize_program

pipeline = OptimizationPipeline((MyFunctionPass(), AnotherPass()))
optimized = optimize_program(program, pipeline=pipeline)
```

An optimisation pass implements the `OptimizationPass` protocol: it has a stable
`name` and an `optimize(program: Program) -> Program` method. This keeps the
pipeline open to whole-program work such as inlining or global dead-code
elimination. Most passes should subclass `FunctionOptimizationPass` and implement
`optimize_function(...)`; the base class recursively traverses nested function
code, overload sets, object initializers, loop and unfold payloads, and
vector-extension callbacks.

The default pipeline is deliberately a sequence of small passes:

- `ExplicitArgumentOptimizationPass` replaces deterministic, empty-stack
  parameter cycling with direct scalar parameter loads for ownership-trivial
  scalar built-ins. It leaves mixed stack/cycle sourcing, nested cycle scopes,
  control-flow joins, and lifecycle-bearing values unchanged.
- `ConstantFoldingOptimizationPass` evaluates serialisable literal tuples,
  interpolated strings, and a whitelist of pure resolved built-ins. It does not
  fold names rebound anywhere in the program, vectorised calls, or calls that
  require runtime services.
- `SmallFunctionInliningPass` inlines zero-argument constant functions below a
  configurable bytecode-size threshold. The deliberately narrow initial policy
  avoids changing captures, locals, tags, ownership, recursion, or stack-input
  behaviour.
- `BytecodePeepholeOptimizationPass` removes dead scalar `PUSH_CONST`/`POP`
  pairs and resolves literal conditional branches, including statically tagged
  Boolean results.
- `StackShuffleOptimizationPass` canonicalises shuffle labels, removes proven
  identity/empty shuffles, removes copied values immediately popped, and
  composes adjacent physical-stack permutations. It retains shuffles that could
  source values from the conceptual cycling stack.
- `ControlFlowOptimizationPass` threads unconditional jump chains, removes
  unreachable instructions and next-instruction jumps, and retargets `JUMP`,
  conditional/match jumps, and `TRY_BEGIN` handlers after instruction removal.

Constant folding runs again after inlining so newly exposed literals can be
collapsed. A pass must preserve stack effects, selected overloads, element/data
tags, panics, ownership, source-visible evaluation order, and serialization. It
must not parse lint messages or rediscover facts that belong to analysis.

Every optimisation family has three test layers: focused bytecode/unit tests in
`tests/test_optimizer.py`, differential generated cases in the `optimizer` fuzz
target, and checked-in workload examples under `samples/optimizations/` tested
by `tests/test_programs.py`. Each layer compares unoptimised,
optimised, and serialized execution and also asserts that the intended rewrite
actually happened.

## Future typed rewrites

Some optimisations will need facts that are easier to express before bytecode,
such as a proven identity cast or a structured unreachable-source suffix. Those
should remain a distinct typed pass rather than being hidden inside lint
emission. A safe progression is:

1. detect the pattern during analysis;
2. record stable `LintFinding` and `LintRewrite` facts;
3. add an executable typed rewrite plan that does not depend on rendered text;
4. lower the rewritten typed structure through the normal compiler; and
5. validate original and optimised programs with differential tests.

Enabling optimisation must never change which diagnostics are reported. The
current bytecode optimiser intentionally does not consume lint messages or
`replacement` display text.

## Additional built-in guidance

The default registry also includes conservative rules for common source idioms:

- `unused-loop-index` removes an unread `foreach` index binding.
- `constant-never-reassigned` recommends `const` for a binding written once.
- `captured-write-not-persistent` explains closure capture write semantics.
- `prefer-sum` recognises a single additive accumulator and takes precedence
  over the more general `prefer-fold` rule.
- `prefer-filter` recognises the narrow conditional unchanged-item collection
  shape.
- `explicit-map-can-vectorise` recommends direct vectorisation only when every
  selected mapped element overload permits it and has no element tags.
- `prefer-match` recognises repeated literal equality tests over one variable.
- `while-can-be-foreach` recognises only the index/length/increment traversal
  shape.
- `unknown-lint-code` reports misspelled codes in `@lintOff` and
  `@lintFileOff`.
- `unused-lint-suppression` reports stale specific suppressions. Blanket
  suppressions do not produce this finding.

These rules deliberately prefer false negatives to speculative rewrites. They
remain advisory and can be suppressed using the ordinary lint directives.
