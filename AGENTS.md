Make sure you read the compiler guides in `docs/Compiler Documentation` before making
any changes.
Start with `docs/maintenance/README.md` for the human-facing architecture and
change playbooks, then use the compiler guides for subsystem detail.
For analyser or type-relation work, read `docs/maintenance/type-system.md`
before the exhaustive compiler type-system reference. For code generation,
bytecode, VM, runtime-value, or serialization work, read
`docs/maintenance/runtime-system.md` before the exhaustive runtime reference.

Windows notes for future agents:

- Prefer PowerShell-native commands. Bash-style heredocs such as `python - <<'PY'`
  do not work in PowerShell; use `python -c "..."` or a PowerShell here-string.
- If `uv run ...` fails inside a restricted sandbox with an access-denied error
  while launching `.venv\Scripts\python.exe`, rerun the same command with the
  sandbox disabled or escalated when the current tool policy permits it. Keep
  `$env:UV_CACHE_DIR="$PWD\.uv-cache"` on test/lint commands.
- If Git reports dubious ownership for this checkout, avoid changing global Git
  config. Use a per-command override, for example
  `git -c safe.directory=C:/.../Valiance-Lang status --short`.

When checking the final diff/status to "give you a clean summary of what changed and where", don't worry about running the git commands. Just give the summary based on the
changes, not the git output - that saves time.

When implementing features, don't leave them half finished. "Just for now" type patches lead to legacy cruft that will need to be refactored anyway.

Make language feature implementations as extensible as possible. Don't hardcode tuple layouts, lengths, strings, etc.

`tests/test_programs.py` is a test file containing programs that are fundamental Valiance behaviour. These must always pass after changes. Do not add or modify any tests here (unless of course you're specifically asked to do so).