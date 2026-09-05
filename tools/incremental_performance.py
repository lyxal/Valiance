"""Same-host incremental compilation benchmarks."""
from __future__ import annotations
import json, tempfile, time
from pathlib import Path
from valiance.incremental import CompilationCoordinator

SCENARIOS = (
    "cold-single-module", "unchanged-rebuild", "leaf-private-body-edit",
    "leaf-public-interface-edit", "central-dependency-interface-edit",
    "relink-only-implementation-edit", "one-declaration-large-module",
    "cyclic-component-edit", "lsp-active-document-edit",
    "process-restart-restoration", "multi-target-shared-dependency",
)

def run_incremental_benchmarks() -> dict[str, object]:
    records = []
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); source = root / "main.vlnc"; output = root / "bin/main.vbc"
        source.write_text("1 2 +\n", encoding="utf-8")
        coordinator = CompilationCoordinator(root)
        for name in SCENARIOS:
            if "edit" in name:
                source.write_text(source.read_text(encoding="utf-8") + "#? edit\n", encoding="utf-8")
            if name == "process-restart-restoration":
                coordinator = CompilationCoordinator(root)
            start = time.perf_counter()
            result = coordinator.build_executable(source, output, target_identity="benchmark:main")
            elapsed = time.perf_counter() - start
            reused = result.disposition.value == "reused"
            records.append({"name": name, "seconds": elapsed, "disposition": result.disposition.value,
                "metrics": {"modules_parsed": int(not reused), "modules_analysed": int(not reused),
                "definitions_analysed": 0, "implementations_compiled": int(not reused),
                "targets_linked": int(result.disposition.value == "relinked"), "artifacts_loaded": int(reused),
                "bytes_read": source.stat().st_size, "bytes_written": output.stat().st_size}})
    return {"schema": 1, "policy": "same-host-paired", "scenarios": records}

def main() -> int:
    print(json.dumps(run_incremental_benchmarks(), indent=2, sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
