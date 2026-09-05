"""Run the reproducible release gate and emit a JSON summary."""
from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path

COMMANDS = (
    ("compiled-modules", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_compiled_modules.py"]),
    ("compilation-database", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_compilation_database.py"]),
    ("incremental-fuzz", [sys.executable, "-m", "tools.fuzz", "--target", "incremental-compilation", "--iterations", "100", "--seed", "1511464998"]),
    ("artifact-fuzz", [sys.executable, "-m", "tools.fuzz", "--target", "incremental-artifacts", "--iterations", "100", "--seed", "1511464998"]),
    ("optimizer-replay", [sys.executable, "-m", "tools.fuzz", "--target", "optimizer", "--iterations", "120", "--seed", "12648430"]),
)

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    results = []
    for name, command in COMMANDS:
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=root, text=True, capture_output=True)
        results.append({"name": name, "returncode": completed.returncode,
                        "seconds": time.perf_counter() - started,
                        "stdout": completed.stdout, "stderr": completed.stderr})
    document = {"schema": 1, "python": sys.version, "results": results}
    print(json.dumps(document, indent=2, sort_keys=True))
    return int(any(item["returncode"] for item in results))

if __name__ == "__main__":
    raise SystemExit(main())
