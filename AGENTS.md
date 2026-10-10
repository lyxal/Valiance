# Valiance — Agent Instructions

## 1. Before Making Changes

Understand the relevant architecture before editing code.

- Start with `docs/maintenance/README.md` for the architecture overview, subsystem navigation, and change playbooks.
- Read the relevant maintenance guide before working in a subsystem:
  - **Analyser, types, or type relations:** `docs/maintenance/type-system.md`
  - **Code generation, bytecode, VM, runtime values, or serialization:** `docs/maintenance/runtime-system.md`
- Consult the corresponding detailed references in `docs/Compiler Documentation` before implementing changes.
- Read additional documentation when the change crosses subsystem boundaries. Do not read unrelated exhaustive documentation unnecessarily.
- Inspect existing implementations, interfaces, and tests before designing a solution. Follow established conventions unless there is a concrete reason to improve them.

Documentation describes intended architecture, but may be outdated. When documentation and code disagree, investigate the discrepancy rather than blindly following either.

## 2. Implementation Principles

Implement complete, maintainable solutions rather than temporary workarounds.

- **Finish features end-to-end.** Do not leave partially integrated implementations, placeholder logic, or known gaps in supported behaviour.
- **Respect subsystem boundaries.** Put functionality in the appropriate layer rather than compensating for missing behaviour elsewhere.
- **Prefer general solutions.** Avoid hardcoded tuple layouts, collection lengths, identifiers, strings, type combinations, or other assumptions that should be represented by existing abstractions.
- **Extend existing mechanisms.** Prefer reusable compiler infrastructure over parallel implementations or special-case branches.
- **Preserve invariants.** Identify and maintain the relevant semantic, type-system, bytecode, and runtime invariants.
- **Handle failure paths.** Invalid programs should produce appropriate diagnostics rather than unexpected exceptions, silent miscompilation, or runtime corruption.
- **Keep changes focused.** Avoid unrelated refactoring, formatting churn, speculative abstractions, and unnecessary compatibility layers.
- **Remove superseded logic.** When replacing an implementation, remove obsolete paths rather than retaining redundant behaviour indefinitely.

Do not use a local workaround to conceal a problem in another subsystem. Investigate and fix the underlying cause when it is within the task's scope.

## 3. Language Feature Changes

For new or modified language features, trace the complete path through the compiler and runtime.

Consider which of the following stages are affected:

1. Lexing and parsing
2. AST representation
3. Name resolution and semantic analysis
4. Type checking and type relations
5. Intermediate representations and lowering
6. Code generation and bytecode
7. VM execution and runtime values
8. Serialization and deserialization
9. Diagnostics and error reporting
10. Documentation and tests

Not every change requires modifications to every stage. Explicitly consider each relevant stage so that behaviour is consistent end-to-end.

When applicable, account for nested constructs, generic types, composition with existing features, edge cases, and invalid inputs.

Do not introduce restrictions merely because the initial implementation handles only the simplest case. If a genuine limitation is necessary, make it explicit and ensure unsupported behaviour is rejected predictably.

## 4. Testing and Validation

Validate changes proportionately to their impact. Avoid both insufficient testing and unnecessary repeated test runs.

- Add or update tests in existing test files where they logically belong.
- Cover successful behaviour, relevant edge cases, and expected failures.
- Prefer tests that establish language semantics rather than tests coupled unnecessarily to implementation details.
- Run focused tests during development when practical.
- For changes affecting fundamental language behaviour, run `tests/test_programs.py` and ensure it passes.
- Do not add to or modify `tests/test_programs.py` unless explicitly requested.
- For changes expected to affect existing tests, rerun the relevant tests after implementation and ensure they pass.
- For changes that intentionally alter or invalidate existing behaviour, update affected tests where appropriate and verify the new expected results. Do not preserve obsolete expectations merely to keep tests green.
- For changes unrelated to executable behaviour, avoid unnecessary test runs.
- Do not repeatedly rerun unchanged test suites without a reason. Prefer one final relevant validation pass after the last substantive change.

Never claim tests passed unless they were actually executed. Distinguish between validation that passed, failed, and was not performed.

## 5. Files and Documentation

Keep the repository organized and its documentation consistent with the implementation.

- Place new files in the appropriate existing subdirectory, not the repository root by default.
- Do not create patch-specific or one-off test files. Prefer extending the existing relevant test suite.
- Create a new test file only when there is a genuine organizational reason.
- Update `docs/maintenance/README.md` with links to significant new files or test modules when appropriate.
- Update relevant maintenance guides or compiler documentation when architecture, semantics, invariants, or supported behaviour changes.
- Follow existing file naming, module structure, and import conventions.

Do not create documentation, helper files, or abstractions without a concrete maintenance benefit.

## 6. Windows and Tooling

The primary development environment is Windows with PowerShell.

- Prefer PowerShell-native commands and Windows-compatible paths.
- Bash heredocs such as `python - <<'PY'` do not work in PowerShell. Use `python -c "..."` or PowerShell here-strings instead.
- For test and lint commands using `uv`, set `$env:UV_CACHE_DIR="$PWD\.uv-cache"`.
- If `uv run ...` fails because the restricted sandbox cannot launch `.venv\Scripts\python.exe`, retry outside the sandbox or with elevated permissions only when current tool policy allows it.
- If Git reports dubious ownership, do not change global Git configuration. Use a per-command override such as `git -c safe.directory=C:/.../Valiance-Lang status --short`.
- Do not run Git commands solely to construct a final summary of changes. Summarize the work from the files actually changed and the validation performed.

Do not modify global machine configuration to solve a repository-local tooling problem.

## 7. Working Autonomously

Investigate the problem, establish an appropriate solution, implement it, and validate the result.

- For complex changes, establish the affected components and an implementation approach before editing.
- Resolve routine implementation details by inspecting the codebase and documentation rather than asking unnecessary questions.
- Ask for clarification when a genuine ambiguity would materially change public language semantics, compatibility, or the requested outcome.
- Do not expand the task into unrelated improvements.
- If an attempted approach reveals an architectural issue, reconsider the design rather than stacking additional patches onto it.
- Do not silently weaken requirements, suppress errors, skip necessary integration, or replace a requested implementation with a stub.

Prefer the simplest solution that fully supports the intended semantics and fits the existing architecture.

## 8. Completion and Reporting

Before considering the task complete:

- Verify that the requested behaviour is implemented across all affected layers.
- Check relevant error paths, edge cases, and interactions.
- Confirm appropriate tests and documentation have been updated.
- Run the necessary validation without redundant repetitions.
- Identify any remaining limitations or unverified assumptions.

In the final response, briefly summarize what changed, where it changed, and what validation was performed.

Do not run Git commands solely for the final report. Do not present unimplemented work or unexecuted validation as complete.

## 9. Don't Run Windows FFI Tests Locally

They're always going to fail on local Windows because local Windows doesn't have the needed DLLs. Instead, skip the tests locally and let CI handle them. GitHub Actions is more than capable of handling the Windows FFI tests, so don't waste time trying to get them to pass locally.