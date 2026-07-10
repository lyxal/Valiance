# Lints and future rewrite passes

Valiance lints are non-fatal, actionable diagnostics for source patterns that are
valid but unnecessarily indirect, redundant, or unreachable. They are produced
by the analyser because that is the first stage with both the parsed structure
and the type/overload facts needed to make safe recommendations.

The compiler does **not** currently rewrite programs. The lint representation is
structured so a later optimiser can reuse the same proven facts without parsing
human-facing messages or changing today's compilation pipeline.

## Public analyser results

An `Analyser` exposes two views of the same findings:

- `analyser.lints` is the backwards-compatible list of rendered strings used by
  the CLI and REPL.
- `analyser.lint_findings` contains `LintFinding` records with a stable rule
  code, raw message, source location, originating AST node, and optional
  `LintRewrite` metadata.

The structured types are exported from `valiance.analysis`:

```python
from valiance.analysis import Analyser, LintFinding, LintRewrite, RewriteKind
```

`LintRewrite.semantics_preserving` is true for the current rules. Its
`replacement` field is display text only; it is not an executable text edit.
Future tooling should dispatch on `RewriteKind` and inspect the associated AST
node.

Use `analyser.clear_lints()` when reusing an analyser. This keeps the rendered
and structured collections synchronized.

## Current rules

The initial rules cover:

- identity casts and statically safe checked casts;
- `move(...)` operations that leave the stack unchanged;
- `copy(...)` operations with an empty output list;
- code directly following an explicit `return` or `break` in the same block;
- match cases following an unconditional/default case;
- repeated all-literal match cases; and
- repeated literal alternatives within an `||` pattern.

The match rules intentionally do not treat guarded or destructuring catch-all
patterns as unconditional, and do not equate repeated guards or arbitrary
expression patterns. Those expressions may be effectful or depend on mutable
state, so removing one is not proven safe merely because the ASTs look alike.

## Adding a lint pattern

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

If safety depends on a selected overload, type, or branch fact, emit the rule
after that fact has been resolved. If safety cannot be proven, prefer a warning
without rewrite metadata or do not add the rule.

### 2. Add failing tests before implementation

Place focused analyser tests in `tests/test_analyser.py`. Cover:

1. the positive pattern;
2. a nearby pattern that must not be flagged;
3. the exact actionable message;
4. the stable rule code; and
5. the expected `RewriteKind`.

For a rule likely to encounter many syntax combinations, extend the
`smart-diagnostics` target in `tools/fuzzing.py`. Fuzz properties should verify
that lints remain non-fatal, structured and rendered views agree, and advertised
rewrites are marked semantics-preserving.

### 3. Choose the narrowest analysis point

Common locations are:

- a registered node handler after overload/type resolution;
- `Analyser.analyse_block(...)` for relationships between sibling statements;
- the match analyser for relationships between ordered cases; or
- a small pure helper near the corresponding AST/type helper.

Do not put lint decisions in the parser merely because a pattern is syntactic.
The analyser can still inspect raw AST while retaining access to type and effect
facts, and it keeps all non-fatal semantic advice in one stage.

### 4. Emit a structured finding

Call `_lint(...)` with an actionable message, stable kebab-case code, source
node, and optional rewrite:

```python
self._lint(
    "this copy produces no values and has no effect; remove it",
    node,
    code="no-op-copy",
    rewrite=LintRewrite(RewriteKind.REMOVE_NODE),
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

### 5. Preserve child-analyser findings

Nested functions and call-site analysis use child analysers. Merge findings with
`_extend_lint_findings(...)`, not by extending `lints` directly, so structured
metadata and rendered messages stay synchronized and deduplicated.

### 6. Verify presentation and behaviour

Run the focused analyser tests, the full suite, and the smart-diagnostics fuzz
target. If the message spans lines, add a CLI rendering test so source arrows and
carets remain correct.

## Keeping the optimiser option open

A future optimiser should be a distinct pass over analysed/typed structures,
not hidden inside lint emission. A safe progression is:

1. keep detecting patterns in the analyser;
2. record stable `LintFinding` and `LintRewrite` facts;
3. add typed-node rewrite plans where source AST identity is insufficient;
4. implement an opt-in pass that consumes only supported, semantics-preserving
   rewrite kinds; and
5. validate original and optimised bytecode with differential tests.

The optimiser must preserve stack effects, element/data tags, panics, ownership,
source mappings, and selected overloads. It should never parse `message` or
`replacement`, and enabling optimisation must not change which diagnostics are
reported.

Until that pass exists, rewrite metadata is descriptive only. The normal
compiler continues to lower the original typed AST exactly as before.
