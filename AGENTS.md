Make sure you read the compiler guides in `docs/Compiler Documentation` before making
any changes.

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
